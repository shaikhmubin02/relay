"""Durable state.

SQLite with WAL. Two invariants are enforced by the database itself rather than by
application logic, because application logic is exactly what races:

* ``ux_assignment_confirmed_slot`` -- at most one confirmed assignment per
  (shift, slot). Two simultaneous acceptances cannot both win.
* ``ux_outbox_idempotency`` -- at most one outbox row per idempotency key. A retried
  or replayed workflow cannot send the same message twice.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .config import get_settings

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS runtime_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS org_policy (
    version      INTEGER PRIMARY KEY,
    created_at   TEXT NOT NULL,
    document     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS volunteers (
    id                   TEXT PRIMARY KEY,
    name                 TEXT NOT NULL,
    email                TEXT NOT NULL,
    active               INTEGER NOT NULL DEFAULT 1,
    opted_in             INTEGER NOT NULL DEFAULT 0,
    certifications       TEXT NOT NULL DEFAULT '',
    availability         TEXT NOT NULL DEFAULT '',
    quiet_hours          TEXT NOT NULL DEFAULT '21:00-07:00',
    max_requests_per_week INTEGER NOT NULL DEFAULT 3,
    notes                TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS shifts (
    id           TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    location     TEXT NOT NULL DEFAULT '',
    starts_at    TEXT NOT NULL,
    ends_at      TEXT NOT NULL,
    required_certification TEXT NOT NULL DEFAULT '',
    headcount    INTEGER NOT NULL DEFAULT 1,
    version      INTEGER NOT NULL DEFAULT 1,
    notes        TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS assignments (
    id           TEXT PRIMARY KEY,
    shift_id     TEXT NOT NULL REFERENCES shifts(id),
    volunteer_id TEXT NOT NULL REFERENCES volunteers(id),
    slot_no      INTEGER NOT NULL,
    status       TEXT NOT NULL,
    source       TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    ended_at     TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_assignment_confirmed_slot
    ON assignments(shift_id, slot_no) WHERE status = 'confirmed';

CREATE TABLE IF NOT EXISTS coverage_events (
    id              TEXT PRIMARY KEY,
    source_event_id TEXT NOT NULL,
    kind            TEXT NOT NULL,
    shift_id        TEXT NOT NULL REFERENCES shifts(id),
    volunteer_id    TEXT NOT NULL REFERENCES volunteers(id),
    slot_no         INTEGER NOT NULL,
    note            TEXT NOT NULL DEFAULT '',
    received_at     TEXT NOT NULL,
    shift_version   INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_coverage_event_source ON coverage_events(source_event_id);

CREATE TABLE IF NOT EXISTS workflows (
    id             TEXT PRIMARY KEY,
    event_id       TEXT NOT NULL REFERENCES coverage_events(id),
    shift_id       TEXT NOT NULL REFERENCES shifts(id),
    slot_no        INTEGER NOT NULL,
    state          TEXT NOT NULL,
    reason         TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    next_action_at TEXT,
    wave           INTEGER NOT NULL DEFAULT 0,
    model_calls    INTEGER NOT NULL DEFAULT 0,
    tool_calls     INTEGER NOT NULL DEFAULT 0,
    emails_sent    INTEGER NOT NULL DEFAULT 0,
    model_provider TEXT NOT NULL DEFAULT '',
    shift_version  INTEGER NOT NULL DEFAULT 1
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_workflow_event ON workflows(event_id);
CREATE INDEX IF NOT EXISTS ix_workflow_due ON workflows(next_action_at);

CREATE TABLE IF NOT EXISTS workflow_transitions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    workflow_id TEXT NOT NULL REFERENCES workflows(id),
    at          TEXT NOT NULL,
    from_state  TEXT NOT NULL,
    to_state    TEXT NOT NULL,
    reason      TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS outreach (
    id           TEXT PRIMARY KEY,
    workflow_id  TEXT NOT NULL REFERENCES workflows(id),
    volunteer_id TEXT NOT NULL REFERENCES volunteers(id),
    wave         INTEGER NOT NULL DEFAULT 0,
    status       TEXT NOT NULL,
    jti          TEXT NOT NULL,
    sent_at      TEXT NOT NULL,
    expires_at   TEXT NOT NULL,
    responded_at TEXT,
    outbox_id    TEXT,
    rationale    TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_outreach_jti ON outreach(jti);
CREATE UNIQUE INDEX IF NOT EXISTS ux_outreach_once ON outreach(workflow_id, volunteer_id);

CREATE TABLE IF NOT EXISTS outbox (
    id              TEXT PRIMARY KEY,
    workflow_id     TEXT,
    idempotency_key TEXT NOT NULL,
    to_email        TEXT NOT NULL,
    subject         TEXT NOT NULL,
    body            TEXT NOT NULL,
    kind            TEXT NOT NULL DEFAULT 'outreach',
    status          TEXT NOT NULL,
    attempts        INTEGER NOT NULL DEFAULT 0,
    provider_message_id TEXT,
    error           TEXT,
    created_at      TEXT NOT NULL,
    sent_at         TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_outbox_idempotency ON outbox(idempotency_key);
CREATE INDEX IF NOT EXISTS ix_outbox_status ON outbox(status);

CREATE TABLE IF NOT EXISTS escalations (
    id           TEXT PRIMARY KEY,
    workflow_id  TEXT NOT NULL REFERENCES workflows(id),
    created_at   TEXT NOT NULL,
    question     TEXT NOT NULL,
    blocker      TEXT NOT NULL,
    evidence     TEXT NOT NULL DEFAULT '{}',
    options      TEXT NOT NULL DEFAULT '[]',
    status       TEXT NOT NULL DEFAULT 'open',
    resolution   TEXT,
    resolved_at  TEXT,
    resolved_by  TEXT
);
CREATE INDEX IF NOT EXISTS ix_escalation_status ON escalations(status);

CREATE TABLE IF NOT EXISTS approvals (
    id            TEXT PRIMARY KEY,
    workflow_id   TEXT NOT NULL REFERENCES workflows(id),
    escalation_id TEXT REFERENCES escalations(id),
    action        TEXT NOT NULL,
    params_fingerprint TEXT NOT NULL,
    shift_version INTEGER NOT NULL,
    created_at    TEXT NOT NULL,
    expires_at    TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'granted',
    granted_by    TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    at          TEXT NOT NULL,
    workflow_id TEXT,
    actor       TEXT NOT NULL,
    action      TEXT NOT NULL,
    outcome     TEXT NOT NULL,
    detail      TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS ix_audit_workflow ON audit_log(workflow_id);

CREATE TABLE IF NOT EXISTS receipts (
    id          TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL REFERENCES workflows(id),
    created_at  TEXT NOT NULL,
    outcome     TEXT NOT NULL,
    document    TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_receipt_workflow ON receipts(workflow_id);
"""

_local = threading.local()

# Tools execute on the agent framework's own worker threads, so connections get
# created on threads this module never sees again. Keeping a registry lets a test or
# an evaluation run close every handle before deleting its temporary database.
_registry_lock = threading.Lock()
_open_connections: set[sqlite3.Connection] = set()


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        str(path),
        timeout=15.0,
        isolation_level=None,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=15000")
    with _registry_lock:
        _open_connections.add(conn)
    return conn


def _forget(conn: sqlite3.Connection) -> None:
    with _registry_lock:
        _open_connections.discard(conn)


def connection() -> sqlite3.Connection:
    """One connection per thread, pointed at the configured database."""
    path = get_settings().db_path
    conn = getattr(_local, "conn", None)
    if conn is not None and getattr(_local, "path", None) == str(path):
        return conn
    if conn is not None:
        conn.close()
        _forget(conn)
    conn = _connect(path)
    _local.conn = conn
    _local.path = str(path)
    return conn


def close_connection() -> None:
    """Close this thread's connection."""
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
        _forget(conn)
    _local.conn = None
    _local.path = None


def close_all() -> None:
    """Close every connection this process has opened, on any thread.

    Needed when the database file itself has to go away -- a temporary evaluation
    workspace on Windows will not delete while a handle is open.
    """
    with _registry_lock:
        connections = list(_open_connections)
        _open_connections.clear()
    for conn in connections:
        try:
            conn.close()
        except Exception:  # noqa: BLE001 - already-closed handles are fine
            pass
    _local.conn = None
    _local.path = None


def init_db() -> None:
    connection().executescript(SCHEMA)


def reset_db() -> None:
    """Drop and rebuild. Used by the demo reset endpoint and by tests."""
    conn = connection()
    names = [
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        if not row["name"].startswith("sqlite_")
    ]
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("BEGIN IMMEDIATE")
    try:
        for name in names:
            conn.execute(f"DROP TABLE IF EXISTS {name}")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)


@contextmanager
def write_tx() -> Iterator[sqlite3.Connection]:
    """An immediate (write-reserving) transaction.

    BEGIN IMMEDIATE takes the write lock up front, so two concurrent acceptances
    serialise here instead of interleaving a read-then-write race.
    """
    conn = connection()
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def query(sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
    return list(connection().execute(sql, params))


def query_one(sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
    rows = list(connection().execute(sql, params))
    return rows[0] if rows else None


def execute(sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
    return connection().execute(sql, params)


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def dumps(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, default=str)


def loads(payload: str | None) -> Any:
    if not payload:
        return None
    return json.loads(payload)
