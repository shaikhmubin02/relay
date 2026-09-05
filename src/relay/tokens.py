"""Signed, single-use volunteer response links.

A volunteer never logs in. The link they receive is the credential, so it is
scoped as tightly as possible: one action, one outreach row, one shift version,
one expiry. Single use is enforced by the ``outreach`` row status, not by the
token, because only the database can settle a race between two clicks.
"""

from __future__ import annotations

import base64
import hmac
import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from . import clock
from .config import get_settings

VALID_ACTIONS = {"accept", "decline"}


class TokenError(Exception):
    """Raised when a response token is malformed, forged, or expired."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ResponseToken:
    jti: str
    workflow_id: str
    outreach_id: str
    volunteer_id: str
    shift_id: str
    shift_version: int
    action: str
    expires_at: str

    def payload(self) -> dict[str, Any]:
        return {
            "jti": self.jti,
            "workflow_id": self.workflow_id,
            "outreach_id": self.outreach_id,
            "volunteer_id": self.volunteer_id,
            "shift_id": self.shift_id,
            "shift_version": self.shift_version,
            "action": self.action,
            "expires_at": self.expires_at,
        }


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _sign(body: bytes) -> str:
    secret = get_settings().token_secret.encode("utf-8")
    return _b64encode(hmac.new(secret, body, sha256).digest())


def issue(token: ResponseToken) -> str:
    if token.action not in VALID_ACTIONS:
        raise ValueError(f"unsupported action {token.action}")
    body = json.dumps(token.payload(), sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"{_b64encode(body)}.{_sign(body)}"


def verify(value: str) -> ResponseToken:
    """Check signature and expiry. Everything else is checked against the database."""
    if not value or value.count(".") != 1:
        raise TokenError("malformed", "This link is not valid.")
    encoded_body, signature = value.split(".", 1)
    try:
        body = _b64decode(encoded_body)
    except Exception as exc:  # noqa: BLE001 - any decode failure is the same to a user
        raise TokenError("malformed", "This link is not valid.") from exc

    if not hmac.compare_digest(_sign(body), signature):
        raise TokenError("bad_signature", "This link is not valid.")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise TokenError("malformed", "This link is not valid.") from exc

    try:
        token = ResponseToken(
            jti=payload["jti"],
            workflow_id=payload["workflow_id"],
            outreach_id=payload["outreach_id"],
            volunteer_id=payload["volunteer_id"],
            shift_id=payload["shift_id"],
            shift_version=int(payload["shift_version"]),
            action=payload["action"],
            expires_at=payload["expires_at"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise TokenError("malformed", "This link is not valid.") from exc

    if token.action not in VALID_ACTIONS:
        raise TokenError("malformed", "This link is not valid.")
    if clock.now() > clock.parse(token.expires_at):
        raise TokenError("expired", "This link has expired.")
    return token


def response_url(token: ResponseToken) -> str:
    base = get_settings().public_base_url.rstrip("/")
    return f"{base}/r/{issue(token)}"
