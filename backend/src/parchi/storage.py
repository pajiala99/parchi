"""S3 object layout and presigned URLs.

Object keys carry their own routing information:

    uploads/<owner: 32 hex>/<documentId: ULID>/<sanitised file name>

so the processor can find the DynamoDB record from the S3 event alone.
"""

from __future__ import annotations

import re
from typing import Any

from . import aws
from .auth import is_valid_owner
from .config import settings
from .ids import is_valid_id

PREFIX = "uploads/"
CONTENT_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "application/pdf": ".pdf",
}
_EXTENSION_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".pdf": "application/pdf"}
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_file_name(name: str, content_type: str) -> str:
    base = name.replace("\\", "/").rsplit("/", 1)[-1].strip()
    stem, dot, ext = base.rpartition(".")
    if not dot:
        stem, ext = base, ""
    stem = _UNSAFE.sub("_", stem).strip("._-")[:80] or "document"
    ext = "." + ext.lower() if ext else ""
    if _EXTENSION_TYPES.get(ext) != content_type:
        ext = CONTENT_TYPES.get(content_type, "")
    return f"{stem}{ext}"


def object_key(owner: str, document_id: str, file_name: str, content_type: str) -> str:
    return f"{PREFIX}{owner}/{document_id}/{safe_file_name(file_name, content_type)}"


def parse_object_key(key: str) -> tuple[str, str, str] | None:
    """Return (owner, documentId, file name) for keys this app created, else None."""
    if not key.startswith(PREFIX):
        return None
    parts = key[len(PREFIX) :].split("/")
    if len(parts) != 3:
        return None
    owner, document_id, name = parts
    if not (is_valid_owner(owner) and is_valid_id(document_id) and name):
        return None
    return owner, document_id, name


def presign_upload(key: str, content_type: str, expires_in: int = 300) -> dict[str, Any]:
    """A browser-usable presigned POST. S3 itself enforces type and size."""
    return aws.s3().generate_presigned_post(
        Bucket=settings().upload_bucket,
        Key=key,
        Fields={"Content-Type": content_type},
        Conditions=[
            {"Content-Type": content_type},
            ["content-length-range", 1, settings().max_upload_bytes],
        ],
        ExpiresIn=expires_in,
    )


def presign_download(key: str, file_name: str, content_type: str, expires_in: int = 300) -> str:
    return aws.s3().generate_presigned_url(
        "get_object",
        Params={
            "Bucket": settings().upload_bucket,
            "Key": key,
            "ResponseContentType": content_type,
            "ResponseContentDisposition": f'inline; filename="{file_name}"',
        },
        ExpiresIn=expires_in,
    )


def read_object(bucket: str, key: str) -> bytes:
    return aws.s3().get_object(Bucket=bucket, Key=key)["Body"].read()


def delete_object(key: str) -> None:
    aws.s3().delete_object(Bucket=settings().upload_bucket, Key=key)
