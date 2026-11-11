"""SNS email notifications, one topic for every workspace.

Each workspace subscribes its email with a *filter policy* on the ``owner``
message attribute, so a single topic fans each message out only to the person
who uploaded the document. SNS sends the opt-in confirmation email itself.
"""

from __future__ import annotations

import json
import re
import unicodedata
from decimal import Decimal
from typing import Any

from botocore.exceptions import ClientError

from . import aws, log
from .config import settings

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")

DOC_TYPE_LABELS = {
    "FEE_RECEIPT": "Fee receipt",
    "MEDICAL_BILL": "Medical bill",
    "INVOICE": "Invoice",
    "OTHER": "Receipt",
}


class SharedPendingSubscription(Exception):
    """The address is already subscribed (unconfirmed) for another workspace."""


def is_valid_email(email: str) -> bool:
    return len(email) <= 254 and bool(EMAIL_RE.match(email))


def _filter_policy(owners: set[str]) -> str:
    return json.dumps({"owner": sorted(owners)})


def _owners(subscription_arn: str) -> set[str]:
    attributes = aws.sns().get_subscription_attributes(SubscriptionArn=subscription_arn)["Attributes"]
    policy = json.loads(attributes.get("FilterPolicy") or "{}")
    return set(policy.get("owner", []))


def _find_subscription(email: str) -> str | None:
    paginator = aws.sns().get_paginator("list_subscriptions_by_topic")
    for page in paginator.paginate(TopicArn=settings().topic_arn):
        for subscription in page.get("Subscriptions", []):
            if subscription.get("Protocol") == "email" and subscription.get("Endpoint", "").lower() == email.lower():
                return subscription.get("SubscriptionArn")
    return None


def subscribe(owner: str, email: str) -> str:
    """Subscribe ``email`` to this workspace's notifications and return the subscription ARN.

    SNS allows one subscription per (topic, protocol, endpoint). When the same
    address is used by several workspaces, they share that subscription and
    its filter policy lists every owner.
    """
    try:
        response = aws.sns().subscribe(
            TopicArn=settings().topic_arn,
            Protocol="email",
            Endpoint=email,
            Attributes={"FilterPolicyScope": "MessageAttributes", "FilterPolicy": _filter_policy({owner})},
            ReturnSubscriptionArn=True,
        )
        arn = response["SubscriptionArn"]
    except ClientError as exc:
        error = exc.response.get("Error", {})
        if error.get("Code") != "InvalidParameter" or "already exists" not in error.get("Message", ""):
            raise
        arn = _find_subscription(email) or ""
        if not arn.startswith("arn:"):
            raise SharedPendingSubscription(email) from exc
    # An existing subscription can come back (with another workspace's filter):
    # make sure this workspace is in its filter policy.
    if arn.startswith("arn:"):
        owners = _owners(arn)
        if owner not in owners:
            aws.sns().set_subscription_attributes(
                SubscriptionArn=arn, AttributeName="FilterPolicy", AttributeValue=_filter_policy(owners | {owner})
            )
    return arn


def unsubscribe(subscription_arn: str) -> None:
    if not subscription_arn.startswith("arn:"):
        return
    try:
        aws.sns().unsubscribe(SubscriptionArn=subscription_arn)
    except ClientError as exc:
        log.warn("unsubscribe failed", error=str(exc))


def release(owner: str, subscription_arn: str) -> None:
    """Stop emailing this workspace; unsubscribe only if no other workspace shares the address."""
    if not subscription_arn.startswith("arn:"):
        return
    try:
        others = _owners(subscription_arn) - {owner}
    except ClientError:
        return  # already gone
    if others:
        aws.sns().set_subscription_attributes(
            SubscriptionArn=subscription_arn, AttributeName="FilterPolicy", AttributeValue=_filter_policy(others)
        )
    else:
        unsubscribe(subscription_arn)


def subscription_status(subscription_arn: str | None) -> str:
    """'confirmed', 'pending' or 'none' (deleted, or unconfirmed and expired)."""
    if not subscription_arn or not subscription_arn.startswith("arn:"):
        return "none"
    try:
        attributes = aws.sns().get_subscription_attributes(SubscriptionArn=subscription_arn)["Attributes"]
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in {"NotFound", "NotFoundException", "InvalidParameter"}:
            return "none"
        raise
    return "pending" if attributes.get("PendingConfirmation") == "true" else "confirmed"


def money(amount: Any, currency: str = "INR") -> str:
    if amount is None:
        return "-"
    value = Decimal(str(amount))
    symbol = {"INR": "Rs.", "USD": "$", "EUR": "EUR ", "GBP": "GBP "}.get(currency, f"{currency} ")
    if currency == "INR":
        whole, _, fraction = f"{value:.2f}".partition(".")
        sign = "-" if whole.startswith("-") else ""
        whole = whole.lstrip("-")
        if len(whole) > 3:
            head, tail = whole[:-3], whole[-3:]
            groups = []
            while len(head) > 2:
                groups.insert(0, head[-2:])
                head = head[:-2]
            if head:
                groups.insert(0, head)
            whole = ",".join(groups) + "," + tail
        return f"{sign}{symbol} {whole}.{fraction}"
    return f"{symbol}{value:,.2f}"


def _ascii(text: str, limit: int) -> str:
    """SNS email subjects must be ASCII without line breaks and under 100 characters."""
    flat = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    flat = re.sub(r"[\r\n\t]+", " ", flat).strip()
    return flat if len(flat) <= limit else flat[: limit - 3].rstrip() + "..."


def compose(doc: dict[str, Any]) -> tuple[str, str]:
    name = doc.get("fileName", "document")
    link = f"{settings().app_url}/#/doc/{doc['documentId']}" if settings().app_url else ""
    if doc.get("status") == "FAILED":
        subject = _ascii(f"Parchi: we could not read {name}", 99)
        body = (
            f"Your upload {name} could not be processed.\n\n"
            f"Reason: {doc.get('error', 'unknown error')}\n\n"
            "Try a sharper photo, a PNG/JPEG scan, or a single-page PDF.\n"
        )
    else:
        label = DOC_TYPE_LABELS.get(doc.get("docType", "OTHER"), "Document")
        currency = doc.get("currency", "INR")
        total = money(doc.get("total"), currency)
        vendor = doc.get("vendor") or "unknown vendor"
        subject = _ascii(f"Parchi: {label} from {vendor} filed ({total})", 99)
        confidence = doc.get("confidence")
        rows = [
            ("Document", name),
            ("Type", label),
            ("Vendor", vendor),
            ("Date", doc.get("documentDate") or "-"),
            ("Total", total),
            ("Tax", money(doc.get("tax"), currency) if doc.get("tax") is not None else "-"),
            ("Reference", doc.get("invoiceNumber") or "-"),
            ("Confidence", f"{float(confidence) * 100:.0f}%" if confidence is not None else "-"),
        ]
        width = max(len(k) for k, _ in rows)
        body = "Your document has been read and filed.\n\n"
        body += "\n".join(f"  {k.ljust(width)} : {v}" for k, v in rows) + "\n"
        if doc.get("needsReview"):
            reasons = "; ".join(doc.get("reviewReasons") or []) or "low confidence"
            body += f"\nPlease double-check it: {reasons}.\n"
    if link:
        body += f"\nOpen it in your dashboard: {link}\n"
    body += "\n-- Parchi, a serverless document pipeline on AWS (UCS531 Cloud Computing project)\n"
    return subject, body


def publish_document(owner: str, doc: dict[str, Any]) -> bool:
    """Best effort: a failed email must not fail a document that was filed correctly."""
    if not settings().topic_arn:
        return False
    subject, body = compose(doc)
    try:
        aws.sns().publish(
            TopicArn=settings().topic_arn,
            Subject=subject,
            Message=body,
            MessageAttributes={
                "owner": {"DataType": "String", "StringValue": owner},
                "event": {"DataType": "String", "StringValue": f"document.{doc.get('status', '').lower()}"},
                "documentId": {"DataType": "String", "StringValue": doc["documentId"]},
            },
        )
        return True
    except ClientError as exc:
        log.error("sns publish failed", exc, documentId=doc.get("documentId"))
        return False
