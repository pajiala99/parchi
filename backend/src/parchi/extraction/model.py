"""Engine-neutral extraction result. Textract and Tesseract both produce one."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any


@dataclass
class LineItem:
    description: str
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    amount: Decimal | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "description": self.description,
            "quantity": self.quantity,
            "unitPrice": self.unit_price,
            "amount": self.amount,
        }


@dataclass
class Extraction:
    engine: str
    text: str = ""
    pages: int = 1
    vendor: str | None = None
    vendor_gstin: str | None = None
    invoice_number: str | None = None
    document_date: str | None = None  # ISO 8601 (YYYY-MM-DD)
    receiver: str | None = None
    total: Decimal | None = None
    subtotal: Decimal | None = None
    tax: Decimal | None = None
    currency: str = "INR"
    line_items: list[LineItem] = field(default_factory=list)
    other_fields: list[dict[str, str]] = field(default_factory=list)
    # 0..1 per field, keyed by the API's camelCase field name
    field_confidence: dict[str, float] = field(default_factory=dict)
