"""DynamoDB repository: one table, single-table design.

    pk                 sk                  item
    WS#<owner>         DOC#<documentId>    a document and everything extracted from it
    WS#<owner>         PROFILE             notification email + SNS subscription ARN
    WS#<owner>         QUOTA#<day>#<kind>  daily counter (expires via TTL)
    GLOBAL             QUOTA#<day>#<kind>  account-wide daily counter (expires via TTL)

Document status moves AWAITING_UPLOAD -> PROCESSING -> COMPLETED | FAILED, and
COMPLETED | FAILED -> QUEUED -> PROCESSING when a user asks to reprocess.
Every transition is a conditional write, so duplicate SQS deliveries and races
with a delete cannot corrupt a record.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError

from . import aws
from .config import settings
from .ids import now_iso, today

AWAITING_UPLOAD = "AWAITING_UPLOAD"
QUEUED = "QUEUED"
PROCESSING = "PROCESSING"
COMPLETED = "COMPLETED"
FAILED = "FAILED"
STATUSES = (AWAITING_UPLOAD, QUEUED, PROCESSING, COMPLETED, FAILED)

INTERNAL_FIELDS = frozenset({"pk", "sk", "type", "owner", "expiresAt"})


class QuotaExceeded(Exception):
    def __init__(self, scope: str, limit: int) -> None:
        super().__init__(f"daily {scope} limit of {limit} reached")
        self.scope = scope
        self.limit = limit


class InvalidCursor(ValueError):
    pass


def _ws(owner: str) -> str:
    return f"WS#{owner}"


def doc_key(owner: str, document_id: str) -> dict[str, str]:
    return {"pk": _ws(owner), "sk": f"DOC#{document_id}"}


def to_ddb(value: Any) -> Any:
    """Python value -> DynamoDB-safe value (floats become Decimal, None is dropped)."""
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        return Decimal(str(round(value, 6)))
    if isinstance(value, dict):
        return {k: to_ddb(v) for k, v in value.items() if v is not None}
    if isinstance(value, (list, tuple)):
        return [to_ddb(v) for v in value if v is not None]
    return value


def public(item: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in item.items() if k not in INTERNAL_FIELDS}


def _conditional_failed(exc: ClientError) -> bool:
    return exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"


# ---------------------------------------------------------------- documents


def create_document(owner: str, item: dict[str, Any]) -> None:
    record = {**doc_key(owner, item["documentId"]), "type": "DOC", "owner": owner, **item}
    aws.table().put_item(Item=to_ddb(record), ConditionExpression="attribute_not_exists(pk)")


def get_document(owner: str, document_id: str) -> dict[str, Any] | None:
    return aws.table().get_item(Key=doc_key(owner, document_id)).get("Item")


def encode_cursor(last_key: dict[str, Any] | None) -> str | None:
    if not last_key:
        return None
    raw = json.dumps({"pk": last_key["pk"], "sk": last_key["sk"]}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str, owner: str) -> dict[str, str]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
    except (ValueError, binascii.Error) as exc:
        raise InvalidCursor("cursor is not valid") from exc
    # A cursor is client-controlled input: never let it point at another workspace.
    if not isinstance(data, dict) or data.get("pk") != _ws(owner) or not str(data.get("sk", "")).startswith("DOC#"):
        raise InvalidCursor("cursor does not belong to this workspace")
    return {"pk": data["pk"], "sk": data["sk"]}


def list_documents(
    owner: str, limit: int = 50, cursor: str | None = None, status: str | None = None
) -> tuple[list[dict[str, Any]], str | None]:
    kwargs: dict[str, Any] = {
        "KeyConditionExpression": Key("pk").eq(_ws(owner)) & Key("sk").begins_with("DOC#"),
        "ScanIndexForward": False,  # ULIDs sort by time, so this is newest first
        "Limit": limit,
    }
    if status:
        kwargs["FilterExpression"] = Attr("status").eq(status)
    if cursor:
        kwargs["ExclusiveStartKey"] = decode_cursor(cursor, owner)
    page = aws.table().query(**kwargs)
    return page.get("Items", []), encode_cursor(page.get("LastEvaluatedKey"))


def iter_documents(owner: str, attributes: list[str]) -> Iterator[dict[str, Any]]:
    names = {f"#a{i}": name for i, name in enumerate(attributes)}
    kwargs: dict[str, Any] = {
        "KeyConditionExpression": Key("pk").eq(_ws(owner)) & Key("sk").begins_with("DOC#"),
        "ProjectionExpression": ", ".join(names),
        "ExpressionAttributeNames": names,
    }
    while True:
        page = aws.table().query(**kwargs)
        yield from page.get("Items", [])
        if "LastEvaluatedKey" not in page:
            return
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def _update(
    owner: str,
    document_id: str,
    set_fields: dict[str, Any],
    *,
    remove: tuple[str, ...] = (),
    condition: str = "attribute_exists(pk)",
    condition_values: dict[str, Any] | None = None,
    condition_names: dict[str, str] | None = None,
    add_sets: dict[str, set[str]] | None = None,
) -> dict[str, Any] | None:
    """SET/REMOVE/ADD in one conditional write. Returns the new item, or None if the condition failed."""
    names: dict[str, str] = dict(condition_names or {})
    values: dict[str, Any] = dict(condition_values or {})
    sets, removes = [], []
    for i, (name, value) in enumerate(set_fields.items()):
        names[f"#s{i}"] = name
        if value is None:
            removes.append(f"#s{i}")
        else:
            values[f":s{i}"] = to_ddb(value)
            sets.append(f"#s{i} = :s{i}")
    for i, name in enumerate(remove):
        names[f"#r{i}"] = name
        removes.append(f"#r{i}")
    adds = []
    for i, (name, members) in enumerate((add_sets or {}).items()):
        names[f"#a{i}"] = name
        values[f":a{i}"] = set(members)
        adds.append(f"#a{i} :a{i}")
    expression = ""
    if sets:
        expression += "SET " + ", ".join(sets)
    if removes:
        expression += " REMOVE " + ", ".join(removes)
    if adds:
        expression += " ADD " + ", ".join(adds)
    kwargs: dict[str, Any] = {
        "Key": doc_key(owner, document_id),
        "UpdateExpression": expression.strip(),
        "ConditionExpression": condition,
        "ExpressionAttributeNames": names,
        "ReturnValues": "ALL_NEW",
    }
    if values:
        kwargs["ExpressionAttributeValues"] = values
    try:
        return aws.table().update_item(**kwargs)["Attributes"]
    except ClientError as exc:
        if _conditional_failed(exc):
            return None
        raise


def lease_seconds() -> int:
    raw = os.environ.get("PROCESSING_LEASE_SECONDS", "")
    return int(raw) if raw.isdigit() else 120


def begin_processing(owner: str, document_id: str, uploaded_at: str, size: int | None) -> dict[str, Any] | None:
    """Claim a document for processing (a lease, so duplicate deliveries don't run Textract twice).

    Succeeds when the document exists and is waiting, queued, failed, or stuck in
    PROCESSING for longer than the lease. Returns None when it was deleted,
    already completed, or another invocation is working on it right now.
    """
    now = int(time.time())
    started = now_iso()
    expression = (
        "SET #st = :processing, processingStartedAt = :started, updatedAt = :started, "
        "leaseUntil = :lease, uploadedAt = if_not_exists(uploadedAt, :uploaded), "
        "attempts = if_not_exists(attempts, :zero) + :one"
    )
    values: dict[str, Any] = {
        ":processing": PROCESSING,
        ":started": started,
        ":lease": now + lease_seconds(),
        ":uploaded": uploaded_at,
        ":zero": 0,
        ":one": 1,
        ":awaiting": AWAITING_UPLOAD,
        ":queued": QUEUED,
        ":failed": FAILED,
        ":now": now,
    }
    if size is not None:
        expression += ", sizeBytes = :size"
        values[":size"] = size
    try:
        return aws.table().update_item(
            Key=doc_key(owner, document_id),
            UpdateExpression=expression + " REMOVE expiresAt, #err",
            ConditionExpression=(
                "attribute_exists(pk) AND (#st IN (:awaiting, :queued, :failed) "
                "OR (#st = :processing AND leaseUntil < :now))"
            ),
            ExpressionAttributeNames={"#st": "status", "#err": "error"},
            ExpressionAttributeValues=values,
            ReturnValues="ALL_NEW",
        )["Attributes"]
    except ClientError as exc:
        if _conditional_failed(exc):
            return None
        raise


def complete(owner: str, document_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
    return _update(
        owner,
        document_id,
        {**fields, "status": COMPLETED, "processedAt": now_iso(), "updatedAt": now_iso()},
        # A fresh extraction replaces earlier corrections (the user is warned before reprocessing).
        remove=("leaseUntil", "error", "lastError", "editedFields", "reviewed", "reviewedAt"),
    )


def fail(owner: str, document_id: str, reason: str, *, extra: dict[str, Any] | None = None) -> dict[str, Any] | None:
    return _update(
        owner,
        document_id,
        {**(extra or {}), "status": FAILED, "error": reason[:500], "failedAt": now_iso(), "updatedAt": now_iso()},
        remove=("leaseUntil",),
    )


def release_for_retry(owner: str, document_id: str, note: str) -> dict[str, Any] | None:
    """A transient failure: hand the document back to the queue for SQS to redeliver."""
    return _update(
        owner,
        document_id,
        {"status": QUEUED, "lastError": note[:500], "updatedAt": now_iso()},
        remove=("leaseUntil",),
    )


def mark_notified(owner: str, document_id: str, delivered: bool) -> None:
    _update(owner, document_id, {"notified": delivered, "notifiedAt": now_iso() if delivered else None})


def queue_reprocess(owner: str, document_id: str) -> dict[str, Any] | None:
    return _update(
        owner,
        document_id,
        {"status": QUEUED, "queuedAt": now_iso(), "updatedAt": now_iso()},
        remove=("error",),
        condition="attribute_exists(pk) AND #st IN (:completed, :failed)",
        condition_names={"#st": "status"},
        condition_values={":completed": COMPLETED, ":failed": FAILED},
    )


def apply_edits(owner: str, document_id: str, edits: dict[str, Any]) -> dict[str, Any] | None:
    """User corrections. Only for finished documents, so the processor never overwrites them."""
    return _update(
        owner,
        document_id,
        {**edits, "reviewed": True, "reviewedAt": now_iso(), "updatedAt": now_iso()},
        condition="attribute_exists(pk) AND #st IN (:completed, :failed)",
        condition_names={"#st": "status"},
        condition_values={":completed": COMPLETED, ":failed": FAILED},
        add_sets={"editedFields": set(edits)},
    )


def delete_document(owner: str, document_id: str) -> dict[str, Any] | None:
    result = aws.table().delete_item(Key=doc_key(owner, document_id), ReturnValues="ALL_OLD")
    return result.get("Attributes")


# ------------------------------------------------------------------ profile


def get_profile(owner: str) -> dict[str, Any]:
    return aws.table().get_item(Key={"pk": _ws(owner), "sk": "PROFILE"}).get("Item") or {}


def save_profile(owner: str, **fields: Any) -> None:
    item = {"pk": _ws(owner), "sk": "PROFILE", "type": "PROFILE", "updatedAt": now_iso(), **fields}
    aws.table().put_item(Item=to_ddb(item))


# ------------------------------------------------------------------- quotas


def _quota_sk(kind: str, day: str) -> str:
    return f"QUOTA#{day}#{kind}"


def consume_quota(owner: str, kind: str, limit: int, global_limit: int | None = None) -> None:
    """Atomically count one use against the workspace (and optionally the global) daily limit.

    A DynamoDB transaction makes both counters move together or not at all.
    """
    day = today()
    expires = int(datetime.now(UTC).timestamp()) + 3 * 86400
    table_name = settings().table_name

    def update(pk: str, cap: int) -> dict[str, Any]:
        return {
            "Update": {
                "TableName": table_name,
                "Key": {"pk": {"S": pk}, "sk": {"S": _quota_sk(kind, day)}},
                "UpdateExpression": "ADD #c :one SET expiresAt = :exp",
                "ConditionExpression": "attribute_not_exists(#c) OR #c < :cap",
                "ExpressionAttributeNames": {"#c": "count"},
                "ExpressionAttributeValues": {
                    ":one": {"N": "1"},
                    ":cap": {"N": str(cap)},
                    ":exp": {"N": str(expires)},
                },
            }
        }

    items = [update(_ws(owner), limit)]
    scopes = [("workspace", limit)]
    if global_limit is not None:
        items.append(update("GLOBAL", global_limit))
        scopes.append(("global", global_limit))
    try:
        aws.dynamodb().transact_write_items(TransactItems=items)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "TransactionCanceledException":
            raise
        reasons = exc.response.get("CancellationReasons") or []
        for (scope, cap), reason in zip(scopes, reasons, strict=False):
            if reason.get("Code") == "ConditionalCheckFailed":
                raise QuotaExceeded(scope, cap) from exc
        raise QuotaExceeded(*scopes[0]) from exc


def quota_used(owner: str, kind: str) -> int:
    item = aws.table().get_item(Key={"pk": _ws(owner), "sk": _quota_sk(kind, today())}).get("Item")
    return int(item.get("count", 0)) if item else 0
