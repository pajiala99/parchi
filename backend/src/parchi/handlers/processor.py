"""The pipeline worker: SQS (fed by S3 upload events) -> OCR -> DynamoDB -> SNS.

Each SQS message carries one S3 ``ObjectCreated`` notification. Messages are
handled independently and failures are reported per message
(``ReportBatchItemFailures``), so one bad file never makes SQS redeliver the
good ones in the same batch.

Failure handling:
* the document itself is unreadable  -> FAILED at once, message acknowledged
* a transient error (throttling...)  -> released, SQS redelivers after the
                                        visibility timeout
* still failing on the last receive  -> FAILED, and SQS moves the message
                                        to the dead-letter queue (alarmed)
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any
from urllib.parse import unquote_plus

from .. import log, notify, storage, store
from ..config import settings
from ..engines import PermanentError, get_engine
from ..extraction import summarize
from ..ids import now_iso


def _ms_between(start_iso: str | None, end_iso: str | None) -> int | None:
    if not start_iso or not end_iso:
        return None
    try:
        start = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
        end = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0, round((end - start).total_seconds() * 1000))


def _notify(owner: str, document: dict[str, Any]) -> None:
    if not store.get_profile(owner).get("email"):
        store.mark_notified(owner, document["documentId"], False)
        return
    delivered = notify.publish_document(owner, document)
    store.mark_notified(owner, document["documentId"], delivered)
    if delivered:
        log.metric("NotificationsPublished", 1)


def process_object(
    bucket: str, key: str, *, size: int | None, event_time: str, final_attempt: bool
) -> dict[str, Any] | None:
    parsed = storage.parse_object_key(key)
    if parsed is None:
        log.warn("ignoring an object outside the upload layout", key=key)
        return None
    owner, document_id, _ = parsed

    document = store.begin_processing(owner, document_id, event_time, size)
    if document is None:
        log.info("skipped: deleted, already filed, or being processed", documentId=document_id)
        log.metric("DuplicateDeliveriesSkipped", 1)
        return None

    started_iso = document.get("processingStartedAt") or now_iso()
    pipeline_start = max(document.get("uploadedAt") or event_time, document.get("queuedAt") or "")
    clock = time.monotonic()
    engine = get_engine()
    try:
        extraction = engine.extract(bucket, key, document.get("contentType", ""))
    except PermanentError as exc:
        log.warn("document unreadable", documentId=document_id, reason=str(exc))
        log.metric("DocumentsFailed", 1, Reason="unreadable")
        failed = store.fail(owner, document_id, str(exc))
        if failed:
            _notify(owner, failed)
        return failed
    except Exception as exc:
        if final_attempt:
            log.metric("DocumentsFailed", 1, Reason="retries_exhausted")
            failed = store.fail(
                owner,
                document_id,
                "Processing kept failing after several retries. The event was moved to the dead-letter queue.",
            )
            if failed:
                _notify(owner, failed)
        else:
            log.metric("TransientErrors", 1)
            store.release_for_retry(owner, document_id, f"{type(exc).__name__}: will retry")
        raise

    extract_ms = round((time.monotonic() - clock) * 1000)
    fields = summarize(extraction)
    finished_iso = now_iso()
    fields["timings"] = {
        "queueMs": _ms_between(pipeline_start, started_iso),
        "extractMs": extract_ms,
        "totalMs": _ms_between(pipeline_start, finished_iso),
    }
    completed = store.complete(owner, document_id, fields)
    if completed is None:
        log.info("document was deleted while it was being processed", documentId=document_id)
        return None

    log.info(
        "document filed",
        documentId=document_id,
        docType=fields["docType"],
        engine=extraction.engine,
        confidence=fields["confidence"],
        needsReview=fields["needsReview"],
        timings=fields["timings"],
    )
    log.metric("DocumentsProcessed", 1, DocType=fields["docType"])
    log.metric("ExtractionLatency", extract_ms, "Milliseconds")
    if fields["timings"]["totalMs"] is not None:
        log.metric("EndToEndLatency", fields["timings"]["totalMs"], "Milliseconds")
    if fields["timings"]["queueMs"] is not None:
        log.metric("QueueDelay", fields["timings"]["queueMs"], "Milliseconds")
    log.metric("ExtractionConfidence", round(fields["confidence"] * 100, 2), "Percent")
    if fields["needsReview"]:
        log.metric("NeedsReview", 1)

    _notify(owner, completed)
    return store.get_document(owner, document_id) or completed


def _process_record(record: dict[str, Any]) -> None:
    body = json.loads(record.get("body") or "{}")
    if body.get("Event") == "s3:TestEvent":
        log.info("ignoring the S3 test event sent when notifications are configured")
        return
    receive_count = int((record.get("attributes") or {}).get("ApproximateReceiveCount", "1"))
    final_attempt = receive_count >= settings().max_receive_count
    for s3_record in body.get("Records", []):
        if not str(s3_record.get("eventName", "")).startswith("ObjectCreated"):
            continue
        s3 = s3_record.get("s3") or {}
        obj = s3.get("object") or {}
        process_object(
            (s3.get("bucket") or {}).get("name", settings().upload_bucket),
            unquote_plus(obj.get("key", "")),  # S3 URL-encodes keys in event notifications
            size=obj.get("size"),
            event_time=s3_record.get("eventTime") or now_iso(),
            final_attempt=final_attempt,
        )


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    failures = []
    for record in event.get("Records", []):
        try:
            _process_record(record)
        except Exception as exc:
            log.error("message failed; SQS will redeliver it", exc, messageId=record.get("messageId"))
            failures.append({"itemIdentifier": record.get("messageId", "")})
    return {"batchItemFailures": failures}
