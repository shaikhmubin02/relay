"""Identifier and fingerprint helpers."""

from __future__ import annotations

import hashlib
import json
import secrets
from typing import Any

_ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"


def new_id(prefix: str, length: int = 10) -> str:
    body = "".join(secrets.choice(_ALPHABET) for _ in range(length))
    return f"{prefix}_{body}"


def fingerprint(payload: Any) -> str:
    """Stable hash of a JSON-serialisable payload.

    Used to bind a coordinator approval to the exact action parameters it approved,
    and to build idempotency keys for outbound side effects.
    """
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
