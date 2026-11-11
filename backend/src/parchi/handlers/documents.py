"""Reading, correcting, reprocessing and deleting filed documents, plus workspace stats."""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote

from .. import __version__, aws, log, storage, store
from ..api import ApiError, Request, make_handler
from ..config import settings
from ..extraction.classify import DOC_TYPES
from ..ids import is_valid_id, now_iso

SUMMARY_FIELDS = (
    "documentId", "fileName", "contentType", "sizeBytes", "declaredSize", "status", "docType",
    "vendor", "documentDate", "total", "currency", "confidence", "needsReview", "reviewed",
    "createdAt", "uploadedAt", "processedAt", "error", "engine", "timings", "notified",
)  # fmt: skip


def _summary(item: dict[str, Any]) -> dict[str, Any]:
    return {key: item[key] for key in SUMMARY_FIELDS if key in item}


def _load(request: Request) -> tuple[str, dict[str, Any]]:
    owner = request.owner
    document_id = request.path_params.get("id", "")
    document = store.get_document(owner, document_id) if is_valid_id(document_id) else None
    if not document:
        raise ApiError(404, "not_found", "No document with that id in this workspace.")
    return owner, document


# ------------------------------------------------------------------ routes


def health(request: Request) -> tuple[int, dict[str, Any]]:
    s = settings()
    return 200, {
        "ok": True,
        "service": "parchi",
        "version": __version__,
        "stage": s.stage,
        "region": s.region,
        "engine": s.ocr_engine,
        "time": now_iso(),
        "limits": {
            "maxUploadBytes": s.max_upload_bytes,
            "dailyUploadsPerWorkspace": s.daily_limit_workspace,
        },
    }


def list_documents(request: Request) -> tuple[int, dict[str, Any]]:
    owner = request.owner
    try:
        limit = min(max(int(request.query.get("limit", "50")), 1), 100)
    except ValueError as exc:
        raise ApiError(400, "invalid_limit", "limit must be a number between 1 and 100.") from exc
    status = request.query.get("status") or None
    if status and status not in store.STATUSES:
        raise ApiError(400, "invalid_status", f"status must be one of {', '.join(store.STATUSES)}.")
    try:
        items, cursor = store.list_documents(owner, limit, request.query.get("cursor"), status)
    except store.InvalidCursor as exc:
        raise ApiError(400, "invalid_cursor", str(exc)) from exc
    return 200, {"items": [_summary(item) for item in items], "nextCursor": cursor}


def get_document(request: Request) -> tuple[int, dict[str, Any]]:
    _, document = _load(request)
    body = store.public(document)
    body["previewUrl"] = None
    if document.get("status") != store.AWAITING_UPLOAD:
        body["previewUrl"] = storage.presign_download(
            document["s3Key"],
            storage.safe_file_name(document["fileName"], document["contentType"]),
            document["contentType"],
        )
    return 200, body


def _text(value: Any, field: str, limit: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ApiError(400, "invalid_value", f"{field} must be text.")
    value = " ".join(value.split())
    if len(value) > limit:
        raise ApiError(400, "invalid_value", f"{field} must be {limit} characters or fewer.")
    return value or None


def _money(value: Any, field: str) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ApiError(400, "invalid_value", f"{field} must be a number.")
    try:
        amount = Decimal(str(value)).quantize(Decimal("0.01"))
    except InvalidOperation as exc:
        raise ApiError(400, "invalid_value", f"{field} must be a number.") from exc
    if amount < 0 or amount > Decimal("1000000000"):
        raise ApiError(400, "invalid_value", f"{field} must be between 0 and 1,000,000,000.")
    return amount


def _date(value: Any) -> str | None:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError as exc:
        raise ApiError(400, "invalid_value", "documentDate must be YYYY-MM-DD.") from exc


EDITABLE = {
    "vendor": lambda v: _text(v, "vendor", 160),
    "receiver": lambda v: _text(v, "receiver", 160),
    "invoiceNumber": lambda v: _text(v, "invoiceNumber", 80),
    "notes": lambda v: _text(v, "notes", 1000),
    "documentDate": _date,
    "total": lambda v: _money(v, "total"),
    "subtotal": lambda v: _money(v, "subtotal"),
    "tax": lambda v: _money(v, "tax"),
}


def patch_document(request: Request) -> tuple[int, dict[str, Any]]:
    owner, document = _load(request)
    body = request.json()
    edits: dict[str, Any] = {}
    for field, value in body.items():
        if field == "docType":
            if value not in DOC_TYPES:
                raise ApiError(400, "invalid_value", f"docType must be one of {', '.join(DOC_TYPES)}.")
            edits[field] = value
        elif field in EDITABLE:
            edits[field] = EDITABLE[field](value)
        else:
            raise ApiError(400, "not_editable", f"{field} cannot be edited.")
    if not edits:
        raise ApiError(400, "no_changes", "Send at least one field to change.")
    updated = store.apply_edits(owner, document["documentId"], edits)
    if updated is None:
        raise ApiError(409, "not_ready", "Only documents that have finished processing can be edited.")
    log.info("document edited", documentId=document["documentId"], fields=sorted(edits))
    return 200, store.public(updated)


def delete_document(request: Request) -> tuple[int, None]:
    owner, document = _load(request)
    storage.delete_object(document["s3Key"])
    store.delete_document(owner, document["documentId"])
    log.metric("DocumentsDeleted", 1)
    return 204, None


def reprocess_document(request: Request) -> tuple[int, dict[str, Any]]:
    """Put the document back on the ingest queue as an S3-shaped event."""
    owner, document = _load(request)
    if document.get("status") not in (store.COMPLETED, store.FAILED):
        raise ApiError(409, "busy", "This document is still being processed.")
    try:
        store.consume_quota(owner, "UPLOAD", settings().daily_limit_workspace, settings().daily_limit_global)
    except store.QuotaExceeded as exc:
        raise ApiError(429, "daily_limit", "Today's processing limit has been reached.", scope=exc.scope) from exc
    updated = store.queue_reprocess(owner, document["documentId"])
    if updated is None:
        raise ApiError(409, "busy", "This document is still being processed.")
    message = {
        "Records": [
            {
                "eventVersion": "2.1",
                "eventSource": "parchi:reprocess",
                "awsRegion": settings().region,
                "eventTime": now_iso(),
                "eventName": "ObjectCreated:Reprocess",
                "s3": {
                    "bucket": {"name": settings().upload_bucket},
                    "object": {"key": quote(document["s3Key"]), "size": int(document.get("sizeBytes") or 0) or None},
                },
            }
        ]
    }
    try:
        aws.sqs().send_message(QueueUrl=settings().ingest_queue_url, MessageBody=json.dumps(message))
    except Exception:
        store.fail(owner, document["documentId"], "Could not queue the document for reprocessing.")
        raise
    log.metric("ReprocessRequested", 1)
    return 202, store.public(updated)


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
    return ordered[index]


def stats(request: Request) -> tuple[int, dict[str, Any]]:
    owner = request.owner
    attributes = [
        "status", "docType", "total", "currency", "documentDate", "createdAt",
        "vendor", "needsReview", "timings", "confidence", "engine",
    ]  # fmt: skip
    documents = list(store.iter_documents(owner, attributes))
    completed = [d for d in documents if d.get("status") == store.COMPLETED]
    currencies = Counter(d.get("currency", "INR") for d in completed)
    currency = currencies.most_common(1)[0][0] if currencies else "INR"
    spendable = [d for d in completed if d.get("total") is not None and d.get("currency", "INR") == currency]

    by_type: dict[str, dict[str, Any]] = defaultdict(lambda: {"count": 0, "total": Decimal("0")})
    for d in completed:
        bucket = by_type[d.get("docType", "OTHER")]
        bucket["count"] += 1
        if d in spendable:
            bucket["total"] += d["total"]

    now = datetime.now(UTC)
    months = []
    for back in range(5, -1, -1):
        year, month = now.year, now.month - back
        while month <= 0:
            year, month = year - 1, month + 12
        months.append(f"{year:04d}-{month:02d}")
    by_month = {m: {"month": m, "count": 0, "total": Decimal("0")} for m in months}
    for d in spendable:
        month = (d.get("documentDate") or d.get("createdAt") or "")[:7]
        if month in by_month:
            by_month[month]["count"] += 1
            by_month[month]["total"] += d["total"]

    vendors: dict[str, dict[str, Any]] = defaultdict(lambda: {"count": 0, "total": Decimal("0")})
    for d in spendable:
        if d.get("vendor"):
            vendors[d["vendor"]]["count"] += 1
            vendors[d["vendor"]]["total"] += d["total"]
    top_vendors = sorted(
        ({"vendor": name, **values} for name, values in vendors.items()), key=lambda v: v["total"], reverse=True
    )[:5]

    end_to_end = [float(d["timings"]["totalMs"]) for d in completed if (d.get("timings") or {}).get("totalMs")]
    extract = [float(d["timings"]["extractMs"]) for d in completed if (d.get("timings") or {}).get("extractMs")]
    confidences = [float(d["confidence"]) for d in completed if d.get("confidence") is not None]
    this_month = now.strftime("%Y-%m")

    return 200, {
        "documents": len(documents),
        "byStatus": dict(Counter(d.get("status", "UNKNOWN") for d in documents)),
        "byType": [{"docType": k, **v} for k, v in sorted(by_type.items(), key=lambda kv: -kv[1]["total"])],
        "byMonth": list(by_month.values()),
        "topVendors": top_vendors,
        "currency": currency,
        "totalSpend": sum((d["total"] for d in spendable), Decimal("0")),
        "spendThisMonth": by_month[this_month]["total"],
        "needsReview": sum(1 for d in completed if d.get("needsReview")),
        "latency": {
            "endToEndMsP50": _percentile(end_to_end, 0.5),
            "endToEndMsP90": _percentile(end_to_end, 0.9),
            "extractMsP50": _percentile(extract, 0.5),
        },
        "averageConfidence": round(statistics.fmean(confidences), 4) if confidences else None,
        "usage": {
            "uploadsToday": store.quota_used(owner, "UPLOAD"),
            "dailyLimit": settings().daily_limit_workspace,
        },
    }


ROUTES = {
    "GET /api/health": health,
    "GET /api/documents": list_documents,
    "GET /api/documents/{id}": get_document,
    "PATCH /api/documents/{id}": patch_document,
    "DELETE /api/documents/{id}": delete_document,
    "POST /api/documents/{id}/reprocess": reprocess_document,
    "GET /api/stats": stats,
}
handler = make_handler(ROUTES)
