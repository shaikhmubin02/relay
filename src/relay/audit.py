"""Append-only audit trail.

Every tool call, every denial, and every state change lands here. The receipt shown
to a coordinator is assembled from these rows, so an action Relay did not record is
an action Relay does not claim.
"""

from __future__ import annotations

import re
from typing import Any

from . import clock, store

_EMAIL_RE = re.compile(r"([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*(@[A-Za-z0-9.-]+)")


def redact(value: Any) -> Any:
    """Mask local parts of email addresses in anything destined for a public trace."""
    if isinstance(value, str):
        return _EMAIL_RE.sub(r"\1***\2", value)
    if isinstance(value, dict):
        return {key: redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def record(
    action: str,
    *,
    actor: str,
    outcome: str = "ok",
    workflow_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    store.execute(
        "INSERT INTO audit_log (at, workflow_id, actor, action, outcome, detail) VALUES (?, ?, ?, ?, ?, ?)",
        (clock.now_iso(), workflow_id, actor, action, outcome, store.dumps(detail or {})),
    )


def for_workflow(workflow_id: str) -> list[dict[str, Any]]:
    rows = store.query(
        "SELECT at, actor, action, outcome, detail FROM audit_log WHERE workflow_id = ? ORDER BY id",
        (workflow_id,),
    )
    entries = []
    for row in rows:
        entry = dict(row)
        entry["detail"] = store.loads(entry["detail"]) or {}
        entries.append(entry)
    return entries


def public_for_workflow(workflow_id: str) -> list[dict[str, Any]]:
    return [redact(entry) for entry in for_workflow(workflow_id)]
