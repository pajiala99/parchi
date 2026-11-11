"""Date parsing with Indian (day-first) defaults.

Recognises 08/11/2026, 8-11-26, 2026-11-08, 8 Nov 2026, 08-Nov-2026,
8th November, 2026 and Nov 8, 2026. Ambiguous numeric dates are read day-first
unless the day-first reading is impossible (e.g. 11/25/2026).
"""

from __future__ import annotations

import re
from datetime import date

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}  # fmt: skip
_MON = (
    r"(?P<mon>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
    r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b\.?"
)
_PATTERNS = [
    ("ymd", re.compile(r"(?<!\d)(?P<y>(?:19|20)\d{2})[-/.](?P<m>\d{1,2})[-/.](?P<d>\d{1,2})(?!\d)")),
    (
        "numeric",
        re.compile(r"(?<![\d/.\-])(?P<a>\d{1,2})[-/.](?P<b>\d{1,2})[-/.](?P<y>(?:19|20)\d{2}|\d{2})(?![\d/.]|-\d)"),
    ),
    (
        "d_mon_y",
        re.compile(
            rf"(?<!\d)(?P<d>\d{{1,2}})(?:st|nd|rd|th)?[\s\-/.,]*{_MON}[\s\-/.,']*(?P<y>(?:19|20)\d{{2}}|\d{{2}})(?!\d)",
            re.IGNORECASE,
        ),
    ),
    (
        "mon_d_y",
        re.compile(
            rf"\b{_MON}\s*(?P<d>\d{{1,2}})(?:st|nd|rd|th)?,?\s+(?P<y>(?:19|20)\d{{2}})(?!\d)",
            re.IGNORECASE,
        ),
    ),
]


def _year(raw: str) -> int:
    value = int(raw)
    return value + 2000 if value < 100 else value


def _build(year: int, month: int, day: int, min_year: int, max_year: int) -> date | None:
    if not (min_year <= year <= max_year):
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None


def find_dates(text: str, *, min_year: int = 2000, max_year: int | None = None) -> list[tuple[str, int, int]]:
    """All dates in ``text`` as (ISO date, start, end), in reading order."""
    top = max_year if max_year is not None else date.today().year + 1
    hits: list[tuple[str, int, int]] = []
    taken: list[tuple[int, int]] = []
    for kind, pattern in _PATTERNS:
        for match in pattern.finditer(text):
            if any(match.start() < end and start < match.end() for start, end in taken):
                continue
            parsed: date | None = None
            if kind == "ymd":
                parsed = _build(int(match["y"]), int(match["m"]), int(match["d"]), min_year, top)
            elif kind == "numeric":
                a, b, year = int(match["a"]), int(match["b"]), _year(match["y"])
                parsed = _build(year, b, a, min_year, top)  # day-first, as printed in India
                if parsed is None and a <= 12 < b:
                    parsed = _build(year, a, b, min_year, top)
            else:
                month = _MONTHS[match["mon"][:3].lower()]
                parsed = _build(_year(match["y"]), month, int(match["d"]), min_year, top)
            if parsed is not None:
                hits.append((parsed.isoformat(), match.start(), match.end()))
                taken.append((match.start(), match.end()))
    return sorted(hits, key=lambda hit: hit[1])


def parse_date(text: str | None) -> str | None:
    if not text:
        return None
    hits = find_dates(text)
    return hits[0][0] if hits else None
