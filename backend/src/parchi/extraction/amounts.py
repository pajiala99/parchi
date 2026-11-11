"""Money parsing for Indian receipts (and the usual international formats).

Handles ``₹ 1,23,456.50`` (lakh grouping), ``Rs. 450/-``, ``INR 1,240.00``,
``$12.99`` and plain ``1240.5``, while refusing to read dates (08/11/2026),
times (13:42), percentages (18%), dosages (650mg) or codes (INV2291) as money.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

_CURRENCY = r"(?P<cur>₹|rs\.?|inr|us\$|\$|usd|€|eur|£|gbp)"
_NUMBER = r"(?P<num>\d{1,3}(?:,\d{2,3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)"
AMOUNT_RE = re.compile(
    rf"(?<![\w/.,\-])(?<!\d:)(?:{_CURRENCY}\s*)?{_NUMBER}(?P<suffix>\s*/-)?"
    r"(?![\w/%]|[.,]\d|-\d|:\d|\s*%)",
    re.IGNORECASE,
)
_CURRENCY_CODES = {
    "₹": "INR", "rs": "INR", "rs.": "INR", "inr": "INR",
    "$": "USD", "us$": "USD", "usd": "USD",
    "€": "EUR", "eur": "EUR", "£": "GBP", "gbp": "GBP",
}  # fmt: skip
# OCR often reads 0 as O/o and 1 as l/I inside numbers.
_OCR_ZERO = re.compile(r"(?<=[\d,.])[Oo]|[Oo](?=[\d,.]\d)")
_OCR_ONE = re.compile(r"(?<=\d)[lI]|[lI](?=[\d,]\d)")
MAX_PLAIN_AMOUNT = Decimal("100000000")  # 10 crore; bigger bare numbers are phone numbers or ids


@dataclass(frozen=True)
class Amount:
    value: Decimal
    currency: str | None
    has_decimals: bool
    start: int
    end: int

    @property
    def marked(self) -> bool:
        """Looks deliberately like money: a currency sign, paise, or a /- suffix."""
        return self.currency is not None or self.has_decimals


def fix_ocr_digits(text: str) -> str:
    return _OCR_ONE.sub("1", _OCR_ZERO.sub("0", text))


def find_amounts(text: str) -> list[Amount]:
    found = []
    for match in AMOUNT_RE.finditer(text):
        raw = match.group("num").replace(",", "")
        try:
            value = Decimal(raw)
        except InvalidOperation:
            continue
        cur = match.group("cur")
        currency = _CURRENCY_CODES.get(cur.lower()) if cur else None
        if match.group("suffix") and currency is None:
            currency = "INR"  # "450/-" is an Indian convention
        if currency is None and "." not in raw and value > MAX_PLAIN_AMOUNT:
            continue
        found.append(Amount(value, currency, "." in raw, match.start(), match.end()))
    return found


def parse_amount(text: str | None) -> Decimal | None:
    """The amount in a field value such as Textract's ``ValueDetection.Text``."""
    if not text:
        return None
    amounts = find_amounts(fix_ocr_digits(text))
    if not amounts:
        return None
    marked = [a for a in amounts if a.marked]
    return (marked or amounts)[-1].value


def parse_quantity(text: str | None) -> Decimal | None:
    if not text:
        return None
    match = re.search(r"\d+(?:\.\d+)?", text.replace(",", ""))
    return Decimal(match.group()) if match else None


def detect_currency(text: str) -> str | None:
    lowered = text.lower()
    if "₹" in text or re.search(r"\b(?:rs\.?|inr|rupees?)(?=[\s\d.]|$)", lowered):
        return "INR"
    if re.search(r"\$\s?\d|\busd\b", lowered):
        return "USD"
    if "€" in text or re.search(r"\beur\b", lowered):
        return "EUR"
    if "£" in text or re.search(r"\bgbp\b", lowered):
        return "GBP"
    return None
