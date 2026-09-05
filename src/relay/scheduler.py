"""Deadlines, retries and reconciliation.

Every wait in Relay is a row with a timestamp, not a sleeping coroutine. If the
process dies between sending a request and its deadline, restarting the worker picks
the deadline back up. Nothing is lost because nothing was held in memory.
"""

from __future__ import annotations

import datetime as _dt
import logging
import threading
from typing import Any

from . import audit, clock, messaging, operations, states, store
from .config import get_settings

logger = logging.getLogger(__name__)


def _expire_stale_outreach(now: _dt.datetime) -> int:
    cursor = store.execute(
        "UPDATE outreach SET status = 'expired', responded_at = ? "
        "WHERE status = 'pending' AND expires_at <= ?",
        (clock.iso(now), clock.iso(now)),
    )
    return cursor.rowcount or 0


def _due_workflows(now: _dt.datetime) -> list[dict[str, Any]]:
    rows = store.query(
        "SELECT * FROM workflows WHERE next_action_at IS NOT NULL AND next_action_at <= ? "
        "AND state NOT IN ('confirmed', 'unresolved', 'withdrawn') ORDER BY next_action_at",
        (clock.iso(now),),
    )
    return store.rows_to_dicts(rows)


def _unknown_delivery(workflow_id: str) -> list[dict[str, Any]]:
    return store.rows_to_dicts(
        store.query(
            "SELECT id, to_email, kind, error FROM outbox WHERE workflow_id = ? AND status = 'unknown'",
            (workflow_id,),
        )
    )


def _pending_outreach(workflow_id: str) -> list[dict[str, Any]]:
    return store.rows_to_dicts(
        store.query(
            "SELECT id, volunteer_id, expires_at FROM outreach WHERE workflow_id = ? AND status = 'pending'",
            (workflow_id,),
        )
    )


def _outreach_summary(workflow_id: str) -> dict[str, int]:
    rows = store.query(
        "SELECT status, COUNT(*) AS n FROM outreach WHERE workflow_id = ? GROUP BY status", (workflow_id,)
    )
    return {row["status"]: int(row["n"]) for row in rows}


def _situation(workflow: dict[str, Any]) -> str:
    counts = _outreach_summary(workflow["id"])
    if not counts:
        return "Nobody has been contacted yet."
    parts = []
    if counts.get("declined"):
        parts.append(f"{counts['declined']} declined")
    if counts.get("expired"):
        parts.append(f"{counts['expired']} did not reply in time")
    if counts.get("superseded"):
        parts.append(f"{counts['superseded']} request(s) were withdrawn")
    detail = ", ".join(parts) if parts else "the previous round produced no reply"
    return (
        f"The last outreach round is over: {detail}. The slot is still open and the shift is "
        "getting closer. Decide whether to ask the next eligible volunteer or escalate."
    )


def _reconcile_unknown_deliveries() -> list[dict[str, Any]]:
    """Escalate any gap holding a message whose delivery could not be determined.

    This runs on every pass rather than on the workflow's own schedule. Waiting for a
    25-minute response window to elapse before mentioning that the request may never
    have arrived would waste the only time the coordinator has.
    """
    handled: list[dict[str, Any]] = []
    rows = store.query(
        """
        SELECT DISTINCT o.workflow_id
        FROM outbox o JOIN workflows w ON w.id = o.workflow_id
        WHERE o.status = 'unknown'
          AND w.state NOT IN ('confirmed', 'unresolved', 'withdrawn', 'needs_human')
        """
    )
    for row in rows:
        workflow_id = row["workflow_id"]
        unknown = _unknown_delivery(workflow_id)
        open_escalation = store.query_one(
            "SELECT id FROM escalations WHERE workflow_id = ? AND status = 'open'", (workflow_id,)
        )
        if open_escalation is None:
            operations.escalate_gap(
                workflow_id,
                question=(
                    "Relay could not confirm whether the coverage request reached the volunteer. "
                    "Do you want to try a different volunteer, or contact this one yourself?"
                ),
                blocker="delivery_unknown",
                summary=(
                    f"{len(unknown)} outbound message(s) returned an indeterminate result from the mail "
                    "transport. Relay has not resent them: a resend could double-ask a volunteer who did "
                    "receive the first one, and Relay cannot tell the two cases apart."
                ),
                options=["retry_outreach", "assign_specific_volunteer", "mark_unresolved"],
            )
        store.execute("UPDATE workflows SET next_action_at = NULL WHERE id = ?", (workflow_id,))
        handled.append({"workflow_id": workflow_id, "action": "escalated_delivery_unknown"})
    return handled


def tick(*, run_agent_fn=None, now: _dt.datetime | None = None) -> dict[str, Any]:
    """One pass of the worker. Safe to call as often as you like."""
    from .agent import run_agent as default_run_agent  # noqa: PLC0415 - avoids import cycle

    run_agent_fn = run_agent_fn or default_run_agent
    now = now or clock.now()

    delivery = messaging.flush()
    expired = _expire_stale_outreach(now)
    handled: list[dict[str, Any]] = _reconcile_unknown_deliveries()
    escalated_ids = {item["workflow_id"] for item in handled}

    for workflow in _due_workflows(now):
        workflow_id = workflow["id"]
        if workflow_id in escalated_ids:
            continue

        pending = _pending_outreach(workflow_id)
        if pending:
            next_deadline = min(item["expires_at"] for item in pending)
            store.execute(
                "UPDATE workflows SET next_action_at = ? WHERE id = ?", (next_deadline, workflow_id)
            )
            handled.append({"workflow_id": workflow_id, "action": "still_waiting", "until": next_deadline})
            continue

        situation = None if workflow["state"] == states.DETECTED else _situation(workflow)
        audit.record(
            "worker.advancing",
            actor="system",
            workflow_id=workflow_id,
            detail={"state": workflow["state"], "wave": int(workflow["wave"])},
        )
        # Clear the due marker first: if the agent fails, the worker will not spin on it.
        store.execute("UPDATE workflows SET next_action_at = NULL WHERE id = ?", (workflow_id,))
        run = run_agent_fn(workflow_id, situation=situation)
        if not run.ok:
            retry_at = clock.iso(now + _dt.timedelta(minutes=2))
            store.execute("UPDATE workflows SET next_action_at = ? WHERE id = ?", (retry_at, workflow_id))
            handled.append({"workflow_id": workflow_id, "action": "agent_failed", "retry_at": retry_at})
            continue
        handled.append(
            {"workflow_id": workflow_id, "action": "agent_ran", "tools": run.tool_names()}
        )

    delivery_after = messaging.flush()
    return {
        "at": clock.iso(now),
        "expired_outreach": expired,
        "delivery": {key: delivery.get(key, 0) + delivery_after.get(key, 0) for key in {"sent", "failed", "unknown"}},
        "workflows": handled,
    }


class Worker:
    """Background loop for the web app. The same ``tick`` runs in tests and the CLI."""

    def __init__(self, interval_seconds: int | None = None) -> None:
        self.interval = interval_seconds or get_settings().worker_interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="relay-worker", daemon=True)
        self._thread.start()
        logger.info("Relay worker started (every %ss)", self.interval)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                tick()
            except Exception:  # noqa: BLE001 - a bad tick must not kill the worker
                logger.exception("worker tick failed")
            finally:
                store.close_connection()
