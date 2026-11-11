"""Time-sortable document ids (ULID layout: 48-bit ms timestamp + 80 random bits).

Because ids sort by creation time, a DynamoDB Query on the workspace partition
returns the newest documents first with ScanIndexForward=False and no index.
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime

CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_ALLOWED = frozenset(CROCKFORD)


def new_id(now_ms: int | None = None) -> str:
    timestamp = int(time.time() * 1000) if now_ms is None else now_ms
    value = (timestamp << 80) | int.from_bytes(os.urandom(10), "big")
    chars = []
    for _ in range(26):
        chars.append(CROCKFORD[value & 31])
        value >>= 5
    return "".join(reversed(chars))


def is_valid_id(value: str) -> bool:
    return len(value) == 26 and value[0] in "01234567" and set(value) <= _ALLOWED


def id_timestamp_ms(value: str) -> int:
    number = 0
    for char in value[:10]:
        number = number * 32 + CROCKFORD.index(char)
    return number


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")
