"""Test doubles and builders shared by the test modules."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

from conftest import BUCKET, KEY_A, REGION
from parchi import storage, store
from parchi.auth import owner_from_key
from parchi.extraction.model import Extraction
from parchi.extraction.textract import parse_analyze_expense
from parchi.ids import new_id, now_iso

# ------------------------------------------------- AnalyzeExpense builders


def field(
    kind: str,
    value: str,
    label: str | None = None,
    confidence: float = 99.0,
    currency: str | None = None,
    page: int = 1,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "Type": {"Text": kind, "Confidence": confidence},
        "ValueDetection": {"Text": value, "Confidence": confidence},
        "PageNumber": page,
    }
    if label:
        result["LabelDetection"] = {"Text": label, "Confidence": confidence}
    if currency:
        result["Currency"] = {"Code": currency, "Confidence": 95.0}
    return result


def item(**values: str) -> dict[str, Any]:
    return {
        "LineItemExpenseFields": [
            {"Type": {"Text": kind, "Confidence": 98.0}, "ValueDetection": {"Text": text, "Confidence": 97.0}}
            for kind, text in values.items()
        ]
    }


def expense_response(
    summary: list[dict[str, Any]], items: list[dict[str, Any]] = (), lines: list[str] = (), pages: int = 1
) -> dict[str, Any]:
    return {
        "DocumentMetadata": {"Pages": pages},
        "ExpenseDocuments": [
            {
                "ExpenseIndex": 1,
                "SummaryFields": list(summary),
                "LineItemGroups": [{"LineItemGroupIndex": 1, "LineItems": list(items)}],
                "Blocks": [
                    {"BlockType": "LINE", "Id": f"line-{i}", "Text": text, "Confidence": 99.0}
                    for i, text in enumerate(lines)
                ],
            }
        ],
    }


PHARMACY_GSTIN = "03AABCS1234K1ZQ"
PHARMACY_LINES = [
    "SANJEEVANI MEDICOS",
    "Shop 12, Leela Bhawan Market, Patiala 147001",
    f"GSTIN: {PHARMACY_GSTIN}   D.L. No: PB-PTA-20B-1187",
    "GST INVOICE",
    "Bill No: SM/2611/0457   Date: 08/11/2026",
    "Patient: Aarav Sharma   Dr. R. Mehta",
    "Item Batch Exp Qty Rate Amount",
    "Paracetamol 650 Tab B2341 08/27 2 32.00 64.00",
    "Azithromycin 500 Tab AZ771 05/27 1 118.50 118.50",
    "Cetirizine 10mg Tab CT220 11/27 3 18.00 54.00",
    "Pantoprazole 40 Tab PN915 02/28 2 96.00 192.00",
    "Vitamin D3 60K Cap VD301 09/27 4 37.50 150.00",
    "Total Qty: 12",
    "Sub Total 578.50",
    "CGST @ 2.5% 14.46",
    "SGST @ 2.5% 14.46",
    "Grand Total Rs. 607.42",
]


def pharmacy_response() -> dict[str, Any]:
    return expense_response(
        summary=[
            field("VENDOR_NAME", "SANJEEVANI MEDICOS", confidence=98.7),
            field("VENDOR_NAME", "GST INVOICE", confidence=41.0),
            field("VENDOR_ADDRESS", "Shop 12, Leela Bhawan Market, Patiala 147001", confidence=96.0),
            field("VENDOR_GST_NUMBER", PHARMACY_GSTIN, label="GSTIN:", confidence=97.2),
            field("INVOICE_RECEIPT_ID", "No: SM/2611/0457", label="Bill", confidence=95.5),
            field("INVOICE_RECEIPT_DATE", "08/11/2026", label="Date:", confidence=99.1),
            field("RECEIVER_NAME", "Aarav Sharma", label="Patient:", confidence=93.0),
            field("TOTAL", "12", label="Total Qty:", confidence=88.0),
            field("SUBTOTAL", "578.50", label="Sub Total", confidence=98.0),
            field("TAX", "14.46", label="CGST @ 2.5%", confidence=97.0),
            field("TAX", "14.46", label="SGST @ 2.5%", confidence=96.5),
            field("TOTAL", "Rs. 607.42", label="Grand Total", confidence=98.9, currency="INR"),
            field("OTHER", "PB-PTA-20B-1187", label="D.L. No:", confidence=90.0),
        ],
        items=[
            item(ITEM="Paracetamol 650 Tab", QUANTITY="2", UNIT_PRICE="32.00", PRICE="64.00"),
            item(ITEM="Azithromycin 500 Tab", QUANTITY="1", UNIT_PRICE="118.50", PRICE="118.50"),
            item(ITEM="Cetirizine 10mg Tab", QUANTITY="3", UNIT_PRICE="18.00", PRICE="54.00"),
            item(ITEM="Pantoprazole 40 Tab", QUANTITY="2", UNIT_PRICE="96.00", PRICE="192.00"),
            item(ITEM="Vitamin D3 60K Cap", QUANTITY="4", UNIT_PRICE="37.50", PRICE="150.00"),
        ],
        lines=PHARMACY_LINES,
    )


# ------------------------------------------------------------ fake engine


class FakeEngine:
    """Returns a fixed extraction, or raises, and records every call."""

    name = "fake"

    def __init__(self) -> None:
        self.response: dict[str, Any] = pharmacy_response()
        self.error: BaseException | None = None
        self.hook: Callable[[str, str], None] | None = None
        self.calls: list[tuple[str, str, str]] = []

    def extract(self, bucket: str, key: str, content_type: str) -> Extraction:
        self.calls.append((bucket, key, content_type))
        if self.hook:
            self.hook(bucket, key)
        if self.error:
            raise self.error
        return parse_analyze_expense(self.response, engine=self.name)


# --------------------------------------------------------- event builders


def api(
    handler: Callable[..., dict[str, Any]],
    method: str,
    route: str,
    *,
    key: str | None = KEY_A,
    body: Any = None,
    query: dict[str, str] | None = None,
    doc_id: str | None = None,
    raw_body: str | None = None,
) -> tuple[int, Any]:
    """Invoke an API handler the way API Gateway (HTTP API, payload v2) would."""
    path = route.replace("{id}", doc_id or "")
    headers = {"content-type": "application/json", "user-agent": "pytest"}
    if key is not None:
        headers["X-Parchi-Key"] = key
    event = {
        "version": "2.0",
        "routeKey": f"{method} {route}",
        "rawPath": path,
        "rawQueryString": "",
        "headers": headers,
        "queryStringParameters": query,
        "pathParameters": {"id": doc_id} if doc_id is not None else None,
        "requestContext": {"http": {"method": method, "path": path}, "requestId": "test-request"},
        "body": raw_body if raw_body is not None else (json.dumps(body) if body is not None else None),
        "isBase64Encoded": False,
    }
    response = handler(event, None)
    payload = json.loads(response["body"]) if response.get("body") else None
    return response["statusCode"], payload


def sqs_record(
    key: str,
    *,
    bucket: str = BUCKET,
    size: int | None = 1024,
    receive_count: int = 1,
    event_name: str = "ObjectCreated:Post",
    body: str | None = None,
) -> dict[str, Any]:
    s3_event = {
        "Records": [
            {
                "eventVersion": "2.1",
                "eventSource": "aws:s3",
                "awsRegion": REGION,
                "eventTime": now_iso(),
                "eventName": event_name,
                "s3": {
                    "bucket": {"name": bucket, "arn": f"arn:aws:s3:::{bucket}"},
                    "object": {"key": quote(key), "size": size},
                },
            }
        ]
    }
    return {
        "messageId": str(uuid.uuid4()),
        "receiptHandle": "handle",
        "body": body if body is not None else json.dumps(s3_event),
        "attributes": {"ApproximateReceiveCount": str(receive_count)},
        "eventSource": "aws:sqs",
        "awsRegion": REGION,
    }


def seed_document(
    cloud: Any,
    *,
    key: str = KEY_A,
    name: str = "bill.png",
    content: bytes = b"\x89PNG fake image bytes",
    content_type: str = "image/png",
    now_ms: int | None = None,
) -> tuple[str, str, str]:
    """A document whose upload reached S3 but has not been processed yet."""
    owner = owner_from_key(key)
    document_id = new_id(now_ms)
    s3_key = storage.object_key(owner, document_id, name, content_type)
    now = now_iso()
    store.create_document(
        owner,
        {
            "documentId": document_id,
            "fileName": name,
            "contentType": content_type,
            "declaredSize": len(content),
            "s3Key": s3_key,
            "status": store.AWAITING_UPLOAD,
            "createdAt": now,
            "updatedAt": now,
        },
    )
    cloud.s3.put_object(Bucket=BUCKET, Key=s3_key, Body=content)
    return owner, document_id, s3_key
