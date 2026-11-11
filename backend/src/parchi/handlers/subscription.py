"""GET/PUT/DELETE /api/subscription - the workspace's confirmation-email address."""

from __future__ import annotations

from typing import Any

from .. import log, notify, store
from ..api import ApiError, Request, make_handler
from ..ids import now_iso

MAX_CHANGES_PER_DAY = 5


def get_subscription(request: Request) -> tuple[int, dict[str, Any]]:
    profile = store.get_profile(request.owner)
    email = profile.get("email")
    status = notify.subscription_status(profile.get("subscriptionArn")) if email else "none"
    return 200, {"email": email, "status": status}


def put_subscription(request: Request) -> tuple[int, dict[str, Any]]:
    owner = request.owner
    email = str(request.json().get("email") or "").strip()
    if not notify.is_valid_email(email):
        raise ApiError(400, "invalid_email", "Enter a valid email address.")
    profile = store.get_profile(owner)
    if profile.get("email", "").lower() == email.lower():
        status = notify.subscription_status(profile.get("subscriptionArn"))
        if status == "confirmed":
            return 200, {"email": email, "status": status}
    try:
        # Every subscribe makes AWS send an email, so cap it to stop abuse.
        store.consume_quota(owner, "SUBSCRIBE", MAX_CHANGES_PER_DAY)
    except store.QuotaExceeded as exc:
        raise ApiError(429, "too_many_changes", "Too many email changes today. Try again tomorrow.") from exc
    old_arn = profile.get("subscriptionArn")
    try:
        arn = notify.subscribe(owner, email)
    except notify.SharedPendingSubscription as exc:
        raise ApiError(
            409,
            "pending_elsewhere",
            "That address has an unconfirmed subscription from another workspace. "
            "Click the link in the AWS confirmation email first, then try again.",
        ) from exc
    if old_arn and old_arn != arn:
        notify.release(owner, old_arn)
    store.save_profile(owner, email=email, subscriptionArn=arn, subscribedAt=now_iso())
    status = notify.subscription_status(arn)
    log.info("email subscription requested", status=status)
    return 200, {"email": email, "status": status}


def delete_subscription(request: Request) -> tuple[int, None]:
    owner = request.owner
    profile = store.get_profile(owner)
    if profile.get("subscriptionArn"):
        notify.release(owner, profile["subscriptionArn"])
    store.save_profile(owner)
    return 204, None


ROUTES = {
    "GET /api/subscription": get_subscription,
    "PUT /api/subscription": put_subscription,
    "DELETE /api/subscription": delete_subscription,
}
handler = make_handler(ROUTES)
