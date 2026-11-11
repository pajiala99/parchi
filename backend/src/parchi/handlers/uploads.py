"""POST /api/uploads - hand the browser a presigned POST so the file goes straight to S3.

The API never touches file bytes: API Gateway caps payloads at 10 MB and
Lambda would pay for every byte streamed through it. S3 enforces the declared
content type and the size limit through the POST policy.
"""

from __future__ import annotations

import time
from typing import Any

from .. import log, storage, store
from ..api import ApiError, Request, make_handler
from ..config import settings
from ..ids import new_id, now_iso

UPLOAD_URL_TTL = 300
_ALIASES = {"image/jpg": "image/jpeg", "image/pjpeg": "image/jpeg", "image/x-png": "image/png"}


def create_upload(request: Request) -> tuple[int, dict[str, Any]]:
    owner = request.owner
    body = request.json()
    name = str(body.get("fileName") or "").strip()
    content_type = str(body.get("contentType") or "").strip().lower()
    content_type = _ALIASES.get(content_type, content_type)
    size = body.get("size")
    limit = settings().max_upload_bytes

    if not name or len(name) > 255:
        raise ApiError(400, "invalid_file_name", "fileName is required (255 characters at most).")
    if content_type not in storage.CONTENT_TYPES:
        raise ApiError(
            415,
            "unsupported_type",
            "Upload a JPEG or PNG photo, or a single-page PDF.",
            allowed=sorted(storage.CONTENT_TYPES),
        )
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise ApiError(400, "invalid_size", "size must be the file size in bytes.")
    if size > limit:
        raise ApiError(413, "too_large", f"Files must be {limit // (1024 * 1024)} MB or smaller.", maxBytes=limit)

    try:
        store.consume_quota(owner, "UPLOAD", settings().daily_limit_workspace, settings().daily_limit_global)
    except store.QuotaExceeded as exc:
        log.metric("QuotaRejected", 1, Scope=exc.scope)
        if exc.scope == "global":
            message = (
                f"The demo has reached its daily limit of {exc.limit} documents across all users "
                "(it keeps Textract inside the free tier). Please try again tomorrow."
            )
        else:
            message = f"This workspace has used all {exc.limit} uploads for today. The limit resets at 00:00 UTC."
        raise ApiError(429, "daily_limit", message, scope=exc.scope, limit=exc.limit) from exc

    document_id = new_id()
    key = storage.object_key(owner, document_id, name, content_type)
    now = now_iso()
    store.create_document(
        owner,
        {
            "documentId": document_id,
            "fileName": name,
            "contentType": content_type,
            "declaredSize": size,
            "s3Key": key,
            "status": store.AWAITING_UPLOAD,
            "createdAt": now,
            "updatedAt": now,
            # If the browser never uploads, TTL garbage-collects the record.
            "expiresAt": int(time.time()) + 86400,
        },
    )
    post = storage.presign_upload(key, content_type, expires_in=UPLOAD_URL_TTL)
    log.metric("UploadsRequested", 1)
    log.info("upload url issued", documentId=document_id, contentType=content_type, size=size)
    return 201, {
        "documentId": document_id,
        "upload": {"url": post["url"], "fields": post["fields"]},
        "expiresIn": UPLOAD_URL_TTL,
        "maxBytes": limit,
    }


ROUTES = {"POST /api/uploads": create_upload}
handler = make_handler(ROUTES)
