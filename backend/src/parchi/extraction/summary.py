"""Classify, cross-check and score an extraction, producing the record that gets filed.

The checks are what make the output trustworthy rather than merely parsed:
a GSTIN must pass its checksum, subtotal + tax must equal the total, and the
date must be plausible. Anything that fails is flagged for human review.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from .classify import classify
from .gstin import is_valid_gstin
from .model import Extraction

KEY_FIELDS = (("vendor", "vendor"), ("documentDate", "date"), ("total", "total"))
REVIEW_CONFIDENCE = 0.8


def _tolerance(total: Decimal) -> Decimal:
    return max(Decimal("1.00"), (total * Decimal("0.01")).quantize(Decimal("0.01")))


def _money(value: Decimal | None) -> str:
    return "-" if value is None else f"{value:,.2f}"


def run_checks(extraction: Extraction, today: date | None = None) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    gstin = extraction.vendor_gstin
    if gstin:
        valid = is_valid_gstin(gstin)
        checks.append(
            {
                "name": "gstin_checksum",
                "ok": valid,
                "detail": f"GSTIN {gstin} {'passes' if valid else 'fails'} the mod-36 checksum",
            }
        )
    total = extraction.total
    if total is not None and extraction.subtotal is not None:
        expected = extraction.subtotal + (extraction.tax or Decimal("0"))
        ok = abs(expected - total) <= _tolerance(total)
        checks.append(
            {
                "name": "totals_reconcile",
                "ok": ok,
                "detail": f"subtotal {_money(extraction.subtotal)} + tax {_money(extraction.tax)} = "
                f"{_money(expected)} vs total {_money(total)}",
            }
        )
    elif total is not None:
        amounts = [item.amount for item in extraction.line_items if item.amount is not None]
        if len(amounts) >= 2:
            items_sum = sum(amounts, Decimal("0"))
            tax = extraction.tax or Decimal("0")
            ok = abs(items_sum - total) <= _tolerance(total) or abs(items_sum + tax - total) <= _tolerance(total)
            checks.append(
                {
                    "name": "line_items_sum",
                    "ok": ok,
                    "detail": f"{len(amounts)} line items add up to {_money(items_sum)}"
                    + (f" (+ tax {_money(tax)})" if tax else "")
                    + f" vs total {_money(total)}",
                }
            )
    if extraction.document_date:
        today = today or date.today()
        parsed = date.fromisoformat(extraction.document_date)
        ok = today - timedelta(days=3650) <= parsed <= today + timedelta(days=1)
        checks.append(
            {
                "name": "date_plausible",
                "ok": ok,
                "detail": f"{extraction.document_date} is {'within' if ok else 'outside'} the last 10 years",
            }
        )
    return checks


def summarize(extraction: Extraction, today: date | None = None) -> dict[str, Any]:
    classification = classify(extraction.text, vendor=extraction.vendor)
    checks = run_checks(extraction, today=today)
    present = {
        "vendor": extraction.vendor,
        "documentDate": extraction.document_date,
        "total": extraction.total,
    }
    scores = [extraction.field_confidence.get(key, 0.0) for key, _ in KEY_FIELDS if present[key] is not None]
    missing = [label for key, label in KEY_FIELDS if present[key] is None]
    confidence = (sum(scores) / len(scores)) if scores else 0.0
    confidence *= max(0.0, 1.0 - 0.25 * len(missing))

    reasons = [f"{label} not found" for label in missing]
    reasons += [check["detail"] for check in checks if not check["ok"]]
    if confidence < REVIEW_CONFIDENCE and not missing:
        reasons.append("low extraction confidence")

    return {
        "docType": classification.doc_type,
        "docTypeConfidence": classification.confidence,
        "docTypeSignals": classification.signals,
        "vendor": extraction.vendor,
        "vendorGstin": extraction.vendor_gstin,
        "invoiceNumber": extraction.invoice_number,
        "documentDate": extraction.document_date,
        "receiver": extraction.receiver,
        "total": extraction.total,
        "subtotal": extraction.subtotal,
        "tax": extraction.tax,
        "currency": extraction.currency,
        "lineItems": [item.to_dict() for item in extraction.line_items],
        "otherFields": extraction.other_fields,
        "fieldConfidence": extraction.field_confidence,
        "confidence": round(confidence, 4),
        "checks": checks,
        "needsReview": bool(reasons),
        "reviewReasons": reasons,
        "engine": extraction.engine,
        "pages": extraction.pages,
        "textPreview": extraction.text[:6000],
    }
