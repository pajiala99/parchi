"""Normalise an Amazon Textract ``AnalyzeExpense`` response.

Textract returns *candidates*: SummaryFields typed VENDOR_NAME, TOTAL, TAX, ...
(often several of each, with their printed labels) plus LineItemGroups. This
module picks the right candidate for each field. Indian bills need care: tax is
usually printed as separate CGST and SGST lines, and "Total" may appear for the
quantity column as well as for the amount.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .amounts import detect_currency, parse_amount, parse_quantity
from .dates import parse_date
from .gstin import GSTIN_RE, find_gstins, is_valid_gstin, normalize
from .model import Extraction, LineItem

_FRIENDLY_LABELS = {
    "VENDOR_ADDRESS": "Vendor address",
    "VENDOR_PHONE": "Vendor phone",
    "VENDOR_URL": "Vendor website",
    "RECEIVER_ADDRESS": "Billed to address",
    "DUE_DATE": "Due date",
    "PO_NUMBER": "PO number",
    "PAYMENT_TERMS": "Payment terms",
    "CUSTOMER_NUMBER": "Customer number",
    "ORDER_DATE": "Order date",
    "DISCOUNT": "Discount",
    "SERVICE_CHARGE": "Service charge",
    "AMOUNT_PAID": "Amount paid",
    "AMOUNT_DUE": "Amount due",
}
_TOTAL_LABEL_BONUS = [
    (re.compile(r"grand\s*total|net\s*(?:payable|amount|total)|amount\s*(?:payable|paid)|total\s*amount", re.I), 3.0),
    (re.compile(r"\btotal\b", re.I), 1.0),
]
_TOTAL_LABEL_PENALTY = re.compile(r"sub\s*-?\s*total|total\s*(?:qty|quantity|items?|discount|savings|tax|gst)", re.I)
_TAX_TOTAL_LABEL = re.compile(r"total\s*(?:tax|gst)|gst\s*total|tax\s*amount", re.I)
_TAX_PART_LABEL = re.compile(r"\b(?:cgst|sgst|utgst|igst|cess|vat)\b", re.I)


@dataclass
class _Field:
    type: str
    label: str
    value: str
    confidence: float
    currency: str | None
    page: int | None


def _text(detection: dict[str, Any] | None) -> str:
    return " ".join(((detection or {}).get("Text") or "").split())


def _summary_fields(response: dict[str, Any]) -> list[_Field]:
    fields = []
    for document in response.get("ExpenseDocuments", []):
        for raw in document.get("SummaryFields", []):
            value = _text(raw.get("ValueDetection"))
            if not value:
                continue
            confidence = (raw.get("ValueDetection") or {}).get("Confidence")
            if confidence is None:
                confidence = (raw.get("Type") or {}).get("Confidence", 0.0)
            fields.append(
                _Field(
                    type=((raw.get("Type") or {}).get("Text") or "OTHER").upper(),
                    label=_text(raw.get("LabelDetection")),
                    value=value,
                    confidence=float(confidence) / 100.0,
                    currency=(raw.get("Currency") or {}).get("Code"),
                    page=raw.get("PageNumber"),
                )
            )
    return fields


def _of_type(fields: list[_Field], *types: str) -> list[_Field]:
    return [f for f in fields if f.type in types]


def _best(fields: list[_Field], *types: str) -> _Field | None:
    """Highest-confidence field of the first type (in priority order) that has any."""
    for kind in types:
        candidates = _of_type(fields, kind)
        if candidates:
            return max(candidates, key=lambda f: f.confidence)
    return None


def _clean_id(value: str) -> str:
    value = re.sub(r"^(?:no\.?|number|#|:)+\s*", "", value.strip(), flags=re.I)
    return value.strip(" :#.-")


def _pick_total(fields: list[_Field]) -> tuple[Decimal | None, float | None, str | None]:
    scored = []
    for field in _of_type(fields, "TOTAL"):
        amount = parse_amount(field.value)
        if amount is None:
            continue
        score = field.confidence
        if _TOTAL_LABEL_PENALTY.search(field.label):
            score -= 5
        for pattern, bonus in _TOTAL_LABEL_BONUS:
            if pattern.search(field.label):
                score += bonus
                break
        scored.append((score, amount, field))
    if scored:
        _, amount, field = max(scored, key=lambda item: (item[0], item[1]))
        return amount, field.confidence, field.currency
    for kind in ("AMOUNT_PAID", "AMOUNT_DUE"):
        field = _best(fields, kind)
        if field and (amount := parse_amount(field.value)) is not None:
            return amount, field.confidence * 0.95, field.currency
    return None, None, None


def _pick_tax(fields: list[_Field]) -> tuple[Decimal | None, float | None]:
    taxes = [(f, parse_amount(f.value)) for f in _of_type(fields, "TAX")]
    taxes = [(f, a) for f, a in taxes if a is not None]
    if not taxes:
        return None, None
    totals = [(f, a) for f, a in taxes if _TAX_TOTAL_LABEL.search(f.label)]
    if totals:
        field, amount = max(totals, key=lambda item: item[0].confidence)
        return amount, field.confidence
    parts = {}
    for field, amount in taxes:
        if _TAX_PART_LABEL.search(field.label):
            parts[(field.label.lower(), amount, field.page)] = (field, amount)
    if len(parts) > 1:  # CGST + SGST (+ cess): add the components up
        confidence = min(f.confidence for f, _ in parts.values())
        return sum((a for _, a in parts.values()), Decimal("0")), confidence
    field, amount = max(taxes, key=lambda item: item[0].confidence)
    return amount, field.confidence


def _pick_gstin(fields: list[_Field], text: str) -> str | None:
    for field in sorted(_of_type(fields, "VENDOR_GST_NUMBER", "TAX_PAYER_ID"), key=lambda f: -f.confidence):
        candidate = normalize(field.value)
        match = GSTIN_RE.search(candidate)
        if match:
            return match.group(1)
    candidates = find_gstins(text)
    valid = [c for c in candidates if is_valid_gstin(c)]
    return (valid or candidates or [None])[0]


def _lines(response: dict[str, Any]) -> list[str]:
    seen: set[str] = set()
    lines = []
    for document in response.get("ExpenseDocuments", []):
        for block in document.get("Blocks", []):
            if block.get("BlockType") != "LINE":
                continue
            block_id = block.get("Id") or f"{len(lines)}"
            if block_id in seen:
                continue
            seen.add(block_id)
            text = _text(block)
            if text:
                lines.append(text)
    return lines


def _line_items(response: dict[str, Any]) -> list[LineItem]:
    items = []
    for document in response.get("ExpenseDocuments", []):
        for group in document.get("LineItemGroups", []):
            for line in group.get("LineItems", []):
                values: dict[str, str] = {}
                for field in line.get("LineItemExpenseFields", []):
                    kind = ((field.get("Type") or {}).get("Text") or "").upper()
                    value = _text(field.get("ValueDetection"))
                    if value and kind not in values:
                        values[kind] = value
                description = values.get("ITEM") or ""
                if not description and values.get("EXPENSE_ROW"):
                    description = re.sub(r"(?:\s+[₹$]?[\d.,/-]+)+$", "", values["EXPENSE_ROW"]).strip()
                amount = parse_amount(values.get("PRICE"))
                if not description and amount is None:
                    continue
                items.append(
                    LineItem(
                        description=description[:200],
                        quantity=parse_quantity(values.get("QUANTITY")),
                        unit_price=parse_amount(values.get("UNIT_PRICE")),
                        amount=amount,
                    )
                )
    return items[:100]


def parse_analyze_expense(response: dict[str, Any], engine: str = "textract") -> Extraction:
    fields = _summary_fields(response)
    lines = _lines(response)
    text = "\n".join(lines) or "\n".join(f"{f.label} {f.value}".strip() for f in fields)
    extraction = Extraction(
        engine=engine, text=text, pages=int((response.get("DocumentMetadata") or {}).get("Pages", 1))
    )
    confidence: dict[str, float] = {}

    if vendor := _best(fields, "VENDOR_NAME"):
        extraction.vendor = vendor.value.strip(" ,:-").lstrip(".")
        confidence["vendor"] = vendor.confidence

    for field in sorted(_of_type(fields, "INVOICE_RECEIPT_DATE"), key=lambda f: -f.confidence) + _of_type(
        fields, "ORDER_DATE"
    ):
        if iso := parse_date(field.value):
            extraction.document_date = iso
            confidence["documentDate"] = field.confidence * (1.0 if field.type == "INVOICE_RECEIPT_DATE" else 0.8)
            break

    if invoice := _best(fields, "INVOICE_RECEIPT_ID"):
        extraction.invoice_number = _clean_id(invoice.value)[:80] or None
        if extraction.invoice_number:
            confidence["invoiceNumber"] = invoice.confidence

    total, total_confidence, currency = _pick_total(fields)
    if total is not None:
        extraction.total = total
        confidence["total"] = total_confidence or 0.0

    if subtotal := _best(fields, "SUBTOTAL"):
        extraction.subtotal = parse_amount(subtotal.value)

    tax, tax_confidence = _pick_tax(fields)
    if tax is not None:
        extraction.tax = tax
        confidence["tax"] = tax_confidence or 0.0

    if receiver := _best(fields, "RECEIVER_NAME"):
        extraction.receiver = receiver.value[:160]

    extraction.vendor_gstin = _pick_gstin(fields, text)
    extraction.currency = (
        currency or next((f.currency for f in fields if f.currency), None) or detect_currency(text) or "INR"
    )
    extraction.line_items = _line_items(response)

    others = []
    for field in fields:
        label = _FRIENDLY_LABELS.get(field.type) or (field.label if field.type == "OTHER" else "")
        if label and len(others) < 30:
            others.append({"label": label[:80], "value": field.value[:200]})
    extraction.other_fields = others
    extraction.field_confidence = {k: round(v, 4) for k, v in confidence.items()}
    return extraction
