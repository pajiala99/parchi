"""GSTIN (Indian GST registration number) detection and checksum validation.

A GSTIN is 15 characters: 2-digit state code, the holder's 10-character PAN,
an entity number, the letter Z, and a mod-36 check character. Checking it
catches OCR misreads that a format regex alone would accept.
"""

from __future__ import annotations

import re

_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
GSTIN_RE = re.compile(r"(?<![A-Z0-9])(\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z])(?![A-Z0-9])")
_VALID_STATES = set(range(1, 39)) | {97, 99}


def checksum_char(first14: str) -> str:
    total = 0
    for index, char in enumerate(first14.upper()):
        product = _CHARS.index(char) * (2 if index % 2 else 1)
        total += product // 36 + product % 36
    return _CHARS[(36 - total % 36) % 36]


def normalize(value: str) -> str:
    return re.sub(r"[\s\-]", "", value).upper()


def is_valid_gstin(value: str | None) -> bool:
    if not value:
        return False
    gstin = normalize(value)
    if not GSTIN_RE.fullmatch(gstin):
        return False
    if int(gstin[:2]) not in _VALID_STATES:
        return False
    return checksum_char(gstin[:14]) == gstin[14]


def find_gstins(text: str) -> list[str]:
    """Candidate GSTINs in reading order (format-valid; checksum not required)."""
    return [m.group(1) for m in GSTIN_RE.finditer(text.upper())]
