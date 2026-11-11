"""Workspace keys.

There are no accounts. The browser generates a random 256-bit workspace key,
keeps it in localStorage and sends it as ``X-Parchi-Key``. The server only ever
sees and stores a hash of it, so a database dump does not reveal any key.
Production would put a Cognito JWT authorizer in front instead (see docs).
"""

from __future__ import annotations

import hashlib
import re

KEY_HEADER = "x-parchi-key"
_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
_OWNER_RE = re.compile(r"^[0-9a-f]{32}$")


def is_valid_key(key: str | None) -> bool:
    return bool(key) and bool(_KEY_RE.match(key or ""))


def owner_from_key(key: str) -> str:
    """128-bit hex owner id derived from the workspace key."""
    return hashlib.sha256(key.encode("ascii")).hexdigest()[:32]


def is_valid_owner(owner: str) -> bool:
    return bool(_OWNER_RE.match(owner))
