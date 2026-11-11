"""Field extraction from raw OCR text (the Tesseract engine has no AnalyzeExpense).

Works line by line, the way a person skims a bill: the shop name is near the
top, the total sits on a line that says "Grand Total" or "Net Payable", tax is
the CGST + SGST lines, and items are the rows between the header and the totals.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from .amounts import Amount, detect_currency, find_amounts, fix_ocr_digits
from .dates import find_dates
from .gstin import find_gstins, is_valid_gstin
from .model import Extraction, LineItem


@dataclass
class Line:
    text: str
    confidence: float = 0.9  # mean OCR word confidence, 0..1


_SKIP_VENDOR = re.compile(
    r"^(?:tax\s+invoice|gst\s+invoice|retail\s+invoice|invoice|(?:fee\s+|cash\s+)?receipt|cash\s+memo|bill"
    r"|estimate|original|duplicate|triplicate|copy|customer\s+copy|welcome|thank)\b",
    re.I,
)
_NOT_VENDOR = re.compile(
    r"\b(?:gstin|gst\s*no|ph(?:one)?|mob(?:ile)?|tel|e-?mail|www\.|address|date|bill\s*no|invoice\s*no|pin)\b|@",
    re.I,
)
_DATE_LINE = re.compile(r"\b(?:date|dated|dt)\b", re.I)
_NOT_DOC_DATE = re.compile(r"\b(?:exp(?:iry)?|mfg|manufactur\w*|valid|due|dob|birth)\b", re.I)
_INVOICE_NO = re.compile(
    r"\b(?P<kind>invoice|inv|bill|receipt|rcpt|voucher|challan|txn|transaction|ref(?:erence)?|order|token|memo)\.?"
    r"\s*(?:no\.?|number|num|#|id)\s*[:.#\-]?\s*(?P<id>[A-Z0-9][A-Z0-9/\-_.]{1,29})",
    re.I,
)
_ID_PRIORITY = {"invoice": 0, "inv": 0, "bill": 1, "receipt": 1, "rcpt": 1, "voucher": 2, "challan": 2, "memo": 2}
_TOTAL_RULES = [
    (re.compile(r"\bgrand\s*total\b", re.I), 10.0),
    (re.compile(r"\bnet\s*(?:payable|amount|amt|total)\b", re.I), 9.0),
    (re.compile(r"\b(?:total\s*)?amount\s*(?:payable|paid|received)\b", re.I), 8.0),
    (re.compile(r"\btotal\s*(?:amount|amt|payable|paid|fees?|bill|due)\b", re.I), 8.0),
    (re.compile(r"\b(?:balance|amount)\s*due\b", re.I), 7.0),
    (re.compile(r"\btotal\b", re.I), 5.0),
]
_NOT_TOTAL = re.compile(
    r"\bsub\s*-?\s*total\b|\btotal\s*(?:qty|quantity|items?|discount|savings|tax|gst|cgst|sgst|igst)\b"
    r"|\btaxable\b|\bin\s+words\b|\brupees\b.*\bonly\b",
    re.I,
)
_SUBTOTAL = re.compile(r"\bsub\s*-?\s*total\b|\btaxable\s*(?:value|amount)\b|\bgross\s*amount\b", re.I)
_TAX_TOTAL = re.compile(r"\btotal\s*(?:tax|gst)\b|\bgst\s*total\b", re.I)
_TAX_PART = re.compile(r"\b(?:cgst|sgst|utgst|igst|vat|cess)\b", re.I)
_ITEM_HEADER = re.compile(r"\b(?:item|description|particulars|medicine|product|service|fee\s*head|details)\b", re.I)
_COLUMN_WORDS = re.compile(r"\b(?:qty|quantity|rate|amount|amt|price|mrp|value)\b", re.I)
_RECEIVER = re.compile(
    r"\b(?:patient(?:'s)?(?:\s*name)?|student(?:'s)?(?:\s*name)?|name\s+of\s+(?:the\s+)?(?:student|patient)"
    r"|received\s+(?:with\s+thanks\s+)?from|bill(?:ed)?\s+to|customer(?:\s*name)?|buyer)\s*[:\-]\s*(?P<name>.+)",
    re.I,
)
_RECEIVER_STOP = re.compile(
    r"\s{2,}|\s+(?:roll|age|sex|dr\.?|doctor|ph|mob|gstin|date|uhid|enrol\w*|programme|class)\b", re.I
)
_CODE_TOKEN = re.compile(r"^(?:[A-Z]{0,3}\d[\dA-Z\-/]*|\d+[./-]\d+(?:[./-]\d+)?)$", re.I)


def _letters(text: str) -> int:
    return sum(char.isalpha() for char in text)


def _respace(text: str) -> str:
    # Re-space "Total1240.00" -> "Total 1240.00"; OCR often drops that gap.
    return re.sub(r"(?<=[A-Za-z:])(?=[₹$]?\d)", " ", fix_ocr_digits(text))


def _amounts(text: str) -> list[Amount]:
    return find_amounts(_respace(text))


def _vendor(lines: list[Line]) -> tuple[str | None, float]:
    for index, line in enumerate(lines[:8]):
        # Trim edge junk but keep a closing "." ("PVT. LTD."); drop stray OCR specks such as
        # a lone "2" or "|" in front of the name.
        text = re.sub(r"^(?:[^A-Za-z\s]{1,2}\s+)+", "", line.text.strip(" :-|*=_~#").lstrip("."))
        compact = text.replace(" ", "")
        if len(text) < 3 or _letters(text) < 3 or _letters(text) / max(len(compact), 1) < 0.6:
            continue
        if _SKIP_VENDOR.match(text) or _NOT_VENDOR.search(text):
            continue
        position_factor = 1.0 if index < 3 else 0.85
        return text[:160], round(min(line.confidence, 0.95) * position_factor, 4)
    return None, 0.0


def _document_date(lines: list[Line]) -> tuple[str | None, float]:
    fallback: tuple[str | None, float] = (None, 0.0)
    for line in lines:
        if _NOT_DOC_DATE.search(line.text) and not _DATE_LINE.search(line.text):
            continue
        hits = find_dates(line.text)
        if not hits:
            continue
        if _DATE_LINE.search(line.text):
            return hits[0][0], round(min(line.confidence, 0.97), 4)
        if fallback[0] is None:
            fallback = (hits[0][0], round(min(line.confidence, 0.97) * 0.8, 4))
    return fallback


def _invoice_number(lines: list[Line]) -> tuple[str | None, float]:
    best: tuple[int, str, float] | None = None
    for line in lines:
        for match in _INVOICE_NO.finditer(line.text):
            value = match.group("id").strip(" .:-/")
            if len(value) < 2 or not re.search(r"\d", value):
                continue
            rank = _ID_PRIORITY.get(match.group("kind").lower(), 3)
            if best is None or rank < best[0]:
                best = (rank, value, line.confidence)
    if best is None:
        return None, 0.0
    return best[1][:80], round(min(best[2], 0.95), 4)


def _value_on_or_after(lines: list[Line], index: int) -> tuple[Amount | None, float]:
    amounts = [a for a in _amounts(lines[index].text) if a.value > 0]
    if amounts:
        return amounts[-1], lines[index].confidence
    # "Grand Total" on one line, the figure alone on the next.
    if index + 1 < len(lines) and _letters(lines[index + 1].text) <= 3:
        following = [a for a in _amounts(lines[index + 1].text) if a.value > 0]
        if following:
            return following[-1], lines[index + 1].confidence
    return None, 0.0


def _total(lines: list[Line]) -> tuple[Decimal | None, float]:
    candidates = []
    for index, line in enumerate(lines):
        if _NOT_TOTAL.search(line.text):
            continue
        weight = next((w for pattern, w in _TOTAL_RULES if pattern.search(line.text)), None)
        if weight is None:
            continue
        amount, confidence = _value_on_or_after(lines, index)
        if amount is None:
            continue
        score = weight + (1.0 if amount.marked else 0.0) + index * 0.01
        candidates.append((score, amount.value, confidence, weight))
    if candidates:
        _, value, confidence, weight = max(candidates, key=lambda c: (c[0], c[1]))
        return value, round(min(confidence, 0.97) * (1.0 if weight >= 8 else 0.85), 4)
    marked = [a for line in lines for a in _amounts(line.text) if a.currency is not None]
    if marked:
        return max(a.value for a in marked), 0.5
    return None, 0.0


def _subtotal(lines: list[Line]) -> Decimal | None:
    for index, line in enumerate(lines):
        if _SUBTOTAL.search(line.text):
            amount, _ = _value_on_or_after(lines, index)
            if amount is not None:
                return amount.value
    return None


def _tax(lines: list[Line]) -> tuple[Decimal | None, float]:
    for index, line in enumerate(lines):
        if _TAX_TOTAL.search(line.text):
            amount, confidence = _value_on_or_after(lines, index)
            if amount is not None:
                return amount.value, round(min(confidence, 0.95), 4)
    parts: dict[tuple[str, Decimal], float] = {}
    for line in lines:
        label = _TAX_PART.search(line.text)
        amounts = _amounts(line.text)
        if label and 1 <= len(amounts) <= 2:
            parts[(label.group().lower(), amounts[-1].value)] = line.confidence
    if not parts:
        return None, 0.0
    return sum((amount for _, amount in parts), Decimal("0")), round(min(min(parts.values()), 0.95) * 0.9, 4)


def _description(tokens: list[str]) -> str:
    end = len(tokens)
    while end > 0:
        token = tokens[end - 1].strip("|:")
        if not token:
            end -= 1
            continue
        if _amounts(token) or _CODE_TOKEN.match(token) or re.fullmatch(r"[x×*@#\-–|]+", token, re.I):
            end -= 1
            continue
        break
    return " ".join(tokens[:end]).strip(" -|:.")


def _line_items(lines: list[Line]) -> list[LineItem]:
    header = next(
        (i for i, line in enumerate(lines) if _ITEM_HEADER.search(line.text) and _COLUMN_WORDS.search(line.text)),
        None,
    )
    start = header + 1 if header is not None else 0
    items = []
    for line in lines[start:]:
        text = _respace(line.text)  # amount positions below index into this same string
        if _SUBTOTAL.search(text) or _TAX_PART.search(text) or _TAX_TOTAL.search(text):
            break
        # Any totals line ("Total Qty", "Grand Total", "Amount Paid"...) ends the item table.
        if any(pattern.search(text) for pattern, _ in _TOTAL_RULES):
            break
        amounts = [a for a in find_amounts(text) if a.value > 0]
        if header is None:
            amounts = [a for a in amounts if a.has_decimals]  # no header: only clear money columns
        if not amounts or _letters(text) < 3:
            continue
        tokens = line.text.split()
        description = _description(tokens)
        if len(description) < 2 or _letters(description) < 2:
            continue
        amount = amounts[-1].value
        unit_price = amounts[-2].value if len(amounts) >= 2 else None
        quantity = None
        tail = text[: amounts[-2].start] if len(amounts) >= 2 else text[: amounts[-1].start]
        # A quantity is a small bare number, never the tail of a date or code such as "05/27".
        qty_match = re.search(r"(?<![\w.,/-])(\d{1,3})(?:\s*[x×])?\s*$", tail.strip())
        if qty_match:
            quantity = Decimal(qty_match.group(1))
        if unit_price is not None and unit_price > amount:
            unit_price = None  # a rate can't exceed the line amount: OCR merged two columns
        if unit_price is not None and quantity is None and amounts[-2].value == amount:
            unit_price = None
        items.append(LineItem(description=description[:200], quantity=quantity, unit_price=unit_price, amount=amount))
    return items[:100]


def _receiver(lines: list[Line]) -> str | None:
    for line in lines:
        match = _RECEIVER.search(line.text)
        if match:
            name = _RECEIVER_STOP.split(match.group("name").strip(), maxsplit=1)[0].strip(" .,:-")
            if _letters(name) >= 2:
                return name[:160]
    return None


def parse_lines(lines: Iterable[Line | str], engine: str = "tesseract", pages: int = 1) -> Extraction:
    rows = [line if isinstance(line, Line) else Line(line) for line in lines]
    rows = [Line(" ".join(row.text.split()), row.confidence) for row in rows if row.text.strip()]
    text = "\n".join(row.text for row in rows)
    extraction = Extraction(engine=engine, text=text, pages=pages)
    confidence: dict[str, float] = {}

    extraction.vendor, vendor_confidence = _vendor(rows)
    if extraction.vendor:
        confidence["vendor"] = vendor_confidence
    extraction.document_date, date_confidence = _document_date(rows)
    if extraction.document_date:
        confidence["documentDate"] = date_confidence
    extraction.invoice_number, id_confidence = _invoice_number(rows)
    if extraction.invoice_number:
        confidence["invoiceNumber"] = id_confidence
    extraction.total, total_confidence = _total(rows)
    if extraction.total is not None:
        confidence["total"] = total_confidence
    extraction.tax, tax_confidence = _tax(rows)
    if extraction.tax is not None:
        confidence["tax"] = tax_confidence
    extraction.subtotal = _subtotal(rows)
    extraction.receiver = _receiver(rows)
    gstins = find_gstins(text)
    valid = [g for g in gstins if is_valid_gstin(g)]
    extraction.vendor_gstin = (valid or gstins or [None])[0]
    extraction.currency = detect_currency(text) or "INR"
    extraction.line_items = _line_items(rows)
    extraction.field_confidence = confidence
    return extraction
