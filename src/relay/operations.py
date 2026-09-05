"""The policy-checked operations that make up Relay's tool surface.

This module holds the enforcement boundary. Everything here re-derives its own facts
from the database and re-applies organisation policy, on the assumption that the
caller -- a language model -- may have been persuaded to ask for something it should
not. There is no Strands import here on purpose: the same functions are called by the
agent, by the HTTP layer, and by tests.
"""

from __future__ import annotations

import datetime as _dt
import sqlite3
from typing import Any

from . import audit, clock, ids, messaging, policy as policy_mod, states, store, tokens
from .config import get_settings


class PolicyDenied(Exception):
    """A requested action is not permitted. Always recorded before it is raised."""

    def __init__(self, code: str, message: str, detail: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail or {}


def _fail(code: str, message: str, **detail: Any) -> dict[str, Any]:
    return {"ok": False, "error": code, "message": message, **detail}


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------


def get_workflow(workflow_id: str) -> sqlite3.Row:
    row = store.query_one("SELECT * FROM workflows WHERE id = ?", (workflow_id,))
    if row is None:
        raise PolicyDenied("unknown_workflow", f"no workflow {workflow_id}")
    return row


def _shift_when(shift: sqlite3.Row) -> str:
    starts = clock.parse(shift["starts_at"])
    ends = clock.parse(shift["ends_at"])
    return f"{starts.strftime('%a %d %b')}, {starts.strftime('%H:%M')}-{ends.strftime('%H:%M')} UTC"


def transition(conn: sqlite3.Connection, workflow_id: str, to_state: str, reason: str) -> None:
    row = conn.execute("SELECT state FROM workflows WHERE id = ?", (workflow_id,)).fetchone()
    if row is None:
        raise PolicyDenied("unknown_workflow", f"no workflow {workflow_id}")
    from_state = row["state"]
    if from_state == to_state:
        conn.execute(
            "UPDATE workflows SET reason = ?, updated_at = ? WHERE id = ?",
            (reason, clock.now_iso(), workflow_id),
        )
        return
    states.check(from_state, to_state)
    conn.execute(
        "UPDATE workflows SET state = ?, reason = ?, updated_at = ? WHERE id = ?",
        (to_state, reason, clock.now_iso(), workflow_id),
    )
    conn.execute(
        "INSERT INTO workflow_transitions (workflow_id, at, from_state, to_state, reason) VALUES (?, ?, ?, ?, ?)",
        (workflow_id, clock.now_iso(), from_state, to_state, reason),
    )


def charge_tool_call(workflow_id: str, tool_name: str) -> None:
    """Bounded autonomy: a runaway loop stops at a hard, per-workflow ceiling."""
    limit = get_settings().max_tool_calls_per_workflow
    row = store.query_one("SELECT tool_calls FROM workflows WHERE id = ?", (workflow_id,))
    used = int(row["tool_calls"]) if row else 0
    if used >= limit:
        audit.record(
            "tool.budget_exceeded",
            actor="system",
            outcome="denied",
            workflow_id=workflow_id,
            detail={"tool": tool_name, "limit": limit},
        )
        raise PolicyDenied("tool_budget_exceeded", f"tool-call budget of {limit} reached for this workflow")
    store.execute("UPDATE workflows SET tool_calls = tool_calls + 1 WHERE id = ?", (workflow_id,))


def _contacted_ids(workflow_id: str) -> set[str]:
    return {
        row["volunteer_id"]
        for row in store.query("SELECT volunteer_id FROM outreach WHERE workflow_id = ?", (workflow_id,))
    }


def candidate_set_for(workflow: sqlite3.Row, *, at: _dt.datetime | None = None) -> policy_mod.CandidateSet:
    event = store.query_one("SELECT * FROM coverage_events WHERE id = ?", (workflow["event_id"],))
    return policy_mod.evaluate_candidates(
        workflow["shift_id"],
        cancelling_volunteer_id=event["volunteer_id"] if event else None,
        already_contacted_ids=_contacted_ids(workflow["id"]),
        at=at,
    )


# ---------------------------------------------------------------------------
# intake
# ---------------------------------------------------------------------------


def record_cancellation(
    *,
    source_event_id: str,
    shift_id: str,
    volunteer_id: str,
    note: str = "",
) -> dict[str, Any]:
    """Authenticated cancellation intake, deduplicated by source event id.

    Replaying the same source event returns the original workflow and starts nothing
    new. This is the first line of defence against duplicate outreach.
    """
    existing = store.query_one(
        "SELECT id FROM coverage_events WHERE source_event_id = ?", (source_event_id,)
    )
    if existing is not None:
        workflow = store.query_one("SELECT * FROM workflows WHERE event_id = ?", (existing["id"],))
        audit.record(
            "intake.duplicate_suppressed",
            actor="system",
            workflow_id=workflow["id"] if workflow else None,
            detail={"source_event_id": source_event_id},
        )
        return {
            "ok": True,
            "duplicate": True,
            "event_id": existing["id"],
            "workflow_id": workflow["id"] if workflow else None,
        }

    shift = store.query_one("SELECT * FROM shifts WHERE id = ?", (shift_id,))
    if shift is None:
        return _fail("unknown_shift", f"no shift {shift_id}")
    volunteer = store.query_one("SELECT * FROM volunteers WHERE id = ?", (volunteer_id,))
    if volunteer is None:
        return _fail("unknown_volunteer", f"no volunteer {volunteer_id}")

    assignment = store.query_one(
        "SELECT * FROM assignments WHERE shift_id = ? AND volunteer_id = ? AND status = 'confirmed'",
        (shift_id, volunteer_id),
    )
    if assignment is None:
        return _fail(
            "no_confirmed_assignment",
            f"{volunteer['name']} is not confirmed on {shift_id}, so there is nothing to cancel",
        )

    event_id = ids.new_id("evt")
    workflow_id = ids.new_id("wf")
    now = clock.now_iso()

    try:
        with store.write_tx() as conn:
            conn.execute(
                """
                INSERT INTO coverage_events (id, source_event_id, kind, shift_id, volunteer_id, slot_no,
                                             note, received_at, shift_version)
                VALUES (?, ?, 'cancellation', ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    source_event_id,
                    shift_id,
                    volunteer_id,
                    int(assignment["slot_no"]),
                    note or "",
                    now,
                    int(shift["version"]),
                ),
            )
            conn.execute(
                "UPDATE assignments SET status = 'cancelled', ended_at = ? WHERE id = ? AND status = 'confirmed'",
                (now, assignment["id"]),
            )
            conn.execute(
                """
                INSERT INTO workflows (id, event_id, shift_id, slot_no, state, reason, created_at, updated_at,
                                       next_action_at, shift_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    workflow_id,
                    event_id,
                    shift_id,
                    int(assignment["slot_no"]),
                    states.DETECTED,
                    f"{volunteer['name']} cancelled",
                    now,
                    now,
                    now,
                    int(shift["version"]),
                ),
            )
            conn.execute(
                "INSERT INTO workflow_transitions (workflow_id, at, from_state, to_state, reason) VALUES (?, ?, '', ?, ?)",
                (workflow_id, now, states.DETECTED, "cancellation received"),
            )
    except sqlite3.IntegrityError:
        # Another thread won the same source event id between our check and insert.
        existing = store.query_one(
            "SELECT id FROM coverage_events WHERE source_event_id = ?", (source_event_id,)
        )
        workflow = store.query_one("SELECT * FROM workflows WHERE event_id = ?", (existing["id"],))
        return {
            "ok": True,
            "duplicate": True,
            "event_id": existing["id"],
            "workflow_id": workflow["id"] if workflow else None,
        }

    audit.record(
        "intake.cancellation",
        actor="system",
        workflow_id=workflow_id,
        detail={
            "source_event_id": source_event_id,
            "shift_id": shift_id,
            "volunteer_id": volunteer_id,
            "slot_no": int(assignment["slot_no"]),
            "note_chars": len(note or ""),
        },
    )
    return {"ok": True, "duplicate": False, "event_id": event_id, "workflow_id": workflow_id}


# ---------------------------------------------------------------------------
# tool 1: load_shift_context
# ---------------------------------------------------------------------------


def load_shift_context(workflow_id: str) -> dict[str, Any]:
    """Versioned shift details, the cancellation as received, and current policy."""
    charge_tool_call(workflow_id, "load_shift_context")
    workflow = get_workflow(workflow_id)
    shift = store.query_one("SELECT * FROM shifts WHERE id = ?", (workflow["shift_id"],))
    event = store.query_one("SELECT * FROM coverage_events WHERE id = ?", (workflow["event_id"],))
    volunteer = store.query_one("SELECT * FROM volunteers WHERE id = ?", (event["volunteer_id"],))
    org = policy_mod.load_policy()

    result = {
        "ok": True,
        "workflow": {
            "id": workflow["id"],
            "state": workflow["state"],
            "wave": int(workflow["wave"]),
            "slot_no": int(workflow["slot_no"]),
        },
        "shift": {
            "id": shift["id"],
            "title": shift["title"],
            "location": shift["location"],
            "starts_at": shift["starts_at"],
            "ends_at": shift["ends_at"],
            "when_human": _shift_when(shift),
            "required_certification": shift["required_certification"],
            "headcount": int(shift["headcount"]),
            "version": int(shift["version"]),
            "notes": shift["notes"],
        },
        "cancellation": {
            "event_id": event["id"],
            "source_event_id": event["source_event_id"],
            "volunteer_id": event["volunteer_id"],
            "volunteer_name": volunteer["name"] if volunteer else "unknown",
            "received_at": event["received_at"],
            # Untrusted. Quoted for interpretation only; it carries no authority.
            "note_from_volunteer": event["note"],
        },
        "policy": {
            "version": org.version,
            "org_name": org.get("org_name"),
            "wave_size": org.wave_size,
            "max_waves": org.max_waves,
            "response_window_minutes": org.response_window_minutes,
            "auto_confirm_when_fully_eligible": org.auto_confirm,
            "certification_is_authoritative": bool(org.get("certification_is_authoritative")),
        },
        "now": clock.now_iso(),
    }
    audit.record(
        "tool.load_shift_context",
        actor="agent",
        workflow_id=workflow_id,
        detail={"shift_id": shift["id"], "shift_version": int(shift["version"])},
    )
    return result


# ---------------------------------------------------------------------------
# tool 2: eligible_volunteers
# ---------------------------------------------------------------------------


def eligible_volunteers(workflow_id: str) -> dict[str, Any]:
    """Apply the organisation's rules in code and return both sides of the decision."""
    charge_tool_call(workflow_id, "eligible_volunteers")
    workflow = get_workflow(workflow_id)
    candidates = candidate_set_for(workflow)
    payload = candidates.public()
    payload["ok"] = True
    audit.record(
        "tool.eligible_volunteers",
        actor="agent",
        workflow_id=workflow_id,
        detail={
            "eligible": [c.volunteer_id for c in candidates.eligible],
            "excluded": {e.volunteer_id: e.codes for e in candidates.excluded},
            "policy_version": candidates.policy_version,
        },
    )
    return payload


# ---------------------------------------------------------------------------
# tool 3: request_coverage
# ---------------------------------------------------------------------------


def request_coverage(
    workflow_id: str,
    volunteer_ids: list[str],
    *,
    personal_note: str = "",
    template_id: str = "coverage_request_v1",
    rationale: str = "",
) -> dict[str, Any]:
    """Ask a bounded set of currently-eligible volunteers to cover the slot.

    The candidate list supplied by the model is treated as a *preference order*, not
    as an authorisation. Anyone not eligible right now is dropped and reported back.
    """
    charge_tool_call(workflow_id, "request_coverage")
    workflow = get_workflow(workflow_id)
    settings = get_settings()
    org = policy_mod.load_policy()

    if workflow["state"] in states.TERMINAL:
        return _fail("workflow_closed", f"workflow is {workflow['state']}; no further outreach")

    if template_id not in messaging.TEMPLATES:
        audit.record(
            "tool.request_coverage",
            actor="agent",
            outcome="denied",
            workflow_id=workflow_id,
            detail={"reason": "unknown_template", "template_id": template_id},
        )
        return _fail("unknown_template", f"{template_id} is not an approved template")

    shift = store.query_one("SELECT * FROM shifts WHERE id = ?", (workflow["shift_id"],))
    if int(shift["version"]) != int(workflow["shift_version"]):
        return _fail(
            "shift_changed",
            "the shift changed since this workflow started; it must be re-evaluated",
            shift_version=int(shift["version"]),
        )
    if not policy_mod.lead_time_ok(shift["starts_at"], clock.now(), org):
        return _fail(
            "insufficient_lead_time",
            f"the shift starts in under {org.min_lead_time_minutes} minutes; a human should decide",
        )

    if int(workflow["wave"]) >= org.max_waves:
        return _fail("wave_limit_reached", f"already ran {org.max_waves} outreach waves for this gap")

    candidates = candidate_set_for(workflow)
    eligible_ids = candidates.eligible_ids()
    requested = [vid for vid in dict.fromkeys(volunteer_ids or [])]
    refused = [vid for vid in requested if vid not in eligible_ids]
    permitted = [vid for vid in requested if vid in eligible_ids]

    # The allowlist is a deployment guard rather than an eligibility rule, so it is
    # applied separately and reported separately. During the hackathon it is what
    # keeps a misconfiguration from reaching a real inbox.
    blocked_recipients = [
        vid
        for vid in permitted
        if not messaging.allowlisted(
            store.query_one("SELECT email FROM volunteers WHERE id = ?", (vid,))["email"]
        )
    ]
    if blocked_recipients:
        permitted = [vid for vid in permitted if vid not in blocked_recipients]
        audit.record(
            "policy.recipient_not_allowlisted",
            actor="system",
            outcome="denied",
            workflow_id=workflow_id,
            detail={"volunteer_ids": blocked_recipients, "allowlist": get_settings().email_allowlist_domains},
        )

    if refused:
        audit.record(
            "policy.outreach_refused",
            actor="system",
            outcome="denied",
            workflow_id=workflow_id,
            detail={
                "refused": refused,
                "reasons": {
                    e.volunteer_id: e.codes for e in candidates.excluded if e.volunteer_id in refused
                },
            },
        )

    if not permitted:
        if blocked_recipients:
            return _fail(
                "recipient_not_allowlisted",
                "every eligible volunteer's address is outside the configured test-recipient allowlist",
                blocked_recipients=blocked_recipients,
                refused=refused,
            )
        return _fail(
            "no_eligible_recipients",
            "none of the requested volunteers are eligible to be contacted right now",
            refused=refused,
            eligible_now=sorted(eligible_ids),
        )

    emails_left = max(0, settings.max_emails_per_workflow - int(workflow["emails_sent"]))
    capped = permitted[: min(org.wave_size, emails_left)]
    dropped_for_cap = permitted[len(capped) :]
    if not capped:
        return _fail("send_budget_exhausted", "per-workflow message budget reached")

    wave = int(workflow["wave"]) + 1
    window = _dt.timedelta(minutes=org.response_window_minutes)
    now = clock.now()
    expires_at = now + window
    sent: list[dict[str, Any]] = []
    already: list[str] = []

    with store.write_tx() as conn:
        for volunteer_id in capped:
            volunteer = conn.execute("SELECT * FROM volunteers WHERE id = ?", (volunteer_id,)).fetchone()
            outreach_id = ids.new_id("otr")
            jti = ids.new_id("jti", 16)
            accept = tokens.ResponseToken(
                jti=jti,
                workflow_id=workflow_id,
                outreach_id=outreach_id,
                volunteer_id=volunteer_id,
                shift_id=shift["id"],
                shift_version=int(shift["version"]),
                action="accept",
                expires_at=clock.iso(expires_at),
            )
            decline = tokens.ResponseToken(**{**accept.payload(), "action": "decline"})

            subject, body = messaging.render(
                template_id,
                {
                    "volunteer_name": volunteer["name"],
                    "org_name": org.get("org_name"),
                    "shift_title": shift["title"],
                    "shift_when": _shift_when(shift),
                    "shift_location": shift["location"] or "at the usual location",
                    "accept_url": tokens.response_url(accept),
                    "decline_url": tokens.response_url(decline),
                    "expires_when": clock.iso(expires_at),
                    "personal_note": personal_note,
                },
            )

            idempotency_key = ids.fingerprint(
                {
                    "workflow_id": workflow_id,
                    "volunteer_id": volunteer_id,
                    "shift_version": int(shift["version"]),
                    "template_id": template_id,
                    "wave": wave,
                }
            )
            try:
                conn.execute(
                    """
                    INSERT INTO outreach (id, workflow_id, volunteer_id, wave, status, jti, sent_at,
                                          expires_at, rationale)
                    VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?)
                    """,
                    (
                        outreach_id,
                        workflow_id,
                        volunteer_id,
                        wave,
                        jti,
                        clock.iso(now),
                        clock.iso(expires_at),
                        (rationale or "")[:400],
                    ),
                )
            except sqlite3.IntegrityError:
                already.append(volunteer_id)
                continue

            outbox_id, created = messaging.enqueue(
                idempotency_key=idempotency_key,
                to_email=volunteer["email"],
                subject=subject,
                body=body,
                kind="outreach",
                workflow_id=workflow_id,
                conn=conn,
            )
            conn.execute("UPDATE outreach SET outbox_id = ? WHERE id = ?", (outbox_id, outreach_id))
            if created:
                conn.execute(
                    "UPDATE workflows SET emails_sent = emails_sent + 1 WHERE id = ?", (workflow_id,)
                )
            sent.append(
                {
                    "volunteer_id": volunteer_id,
                    "name": volunteer["name"],
                    "outreach_id": outreach_id,
                    "outbox_id": outbox_id,
                    "newly_queued": created,
                    "expires_at": clock.iso(expires_at),
                }
            )

        conn.execute(
            "UPDATE workflows SET wave = ?, next_action_at = ? WHERE id = ?",
            (wave, clock.iso(expires_at), workflow_id),
        )
        if sent:
            current = conn.execute("SELECT state FROM workflows WHERE id = ?", (workflow_id,)).fetchone()["state"]
            if current == states.DETECTED:
                transition(conn, workflow_id, states.VALIDATED, "eligibility checked against org policy")
            transition(conn, workflow_id, states.CONTACTING, f"contacting {len(sent)} volunteer(s), wave {wave}")
            transition(
                conn,
                workflow_id,
                states.AWAITING_RESPONSE,
                f"waiting until {clock.iso(expires_at)} for a reply",
            )

    audit.record(
        "tool.request_coverage",
        actor="agent",
        workflow_id=workflow_id,
        detail={
            "template_id": template_id,
            "wave": wave,
            "requested": requested,
            "contacted": [item["volunteer_id"] for item in sent],
            "refused_by_policy": refused,
            "blocked_recipients": blocked_recipients,
            "dropped_for_cap": dropped_for_cap,
            "already_contacted": already,
            "personal_note_used": messaging.sanitise_personal_note(personal_note),
            "expires_at": clock.iso(expires_at),
        },
    )
    return {
        "ok": True,
        "contacted": sent,
        "refused_by_policy": refused,
        "blocked_recipients": blocked_recipients,
        "dropped_for_cap": dropped_for_cap,
        "already_contacted": already,
        "wave": wave,
        "expires_at": clock.iso(expires_at),
        "note": "A request has been queued. This is not coverage until someone accepts.",
    }


# ---------------------------------------------------------------------------
# tool 4: record_acceptance
# ---------------------------------------------------------------------------


def _enqueue_taken_notice(conn, outreach, shift, volunteer, org) -> None:
    subject, body = messaging.render(
        "coverage_taken_v1",
        {
            "volunteer_name": volunteer["name"],
            "org_name": org.get("org_name"),
            "shift_title": shift["title"],
            "shift_when": _shift_when(shift),
        },
    )
    messaging.enqueue(
        idempotency_key=ids.fingerprint({"kind": "taken", "outreach_id": outreach["id"]}),
        to_email=volunteer["email"],
        subject=subject,
        body=body,
        kind="already_covered",
        workflow_id=outreach["workflow_id"],
        conn=conn,
    )


def _closed_outreach_response(conn, outreach, workflow, shift, volunteer, org) -> dict[str, Any]:
    """Answer a click on a request that is no longer open, without misdescribing it.

    A superseded request is one Relay withdrew because the slot filled or the
    coordinator decided. Telling that volunteer "you already responded" would be a
    lie, and they would have no idea whether they are expected on Saturday.
    """
    status = outreach["status"]
    audit.record(
        "response.on_closed_request",
        actor="volunteer",
        workflow_id=outreach["workflow_id"],
        detail={"volunteer_id": outreach["volunteer_id"], "outreach_status": status},
    )
    if status == "accepted":
        return _fail(
            "already_accepted",
            "You already accepted this shift -- you are confirmed. Nothing else is needed.",
            status=status,
            volunteer_name=volunteer["name"],
            shift_title=shift["title"],
            shift_when=_shift_when(shift),
        )
    if status == "declined":
        return _fail(
            "already_declined",
            "You already declined this request. If that was a mistake, contact the coordinator.",
            status=status,
            volunteer_name=volunteer["name"],
        )
    if status == "expired":
        return _fail(
            "request_expired",
            "This request timed out and Relay moved on to someone else. Thank you for coming back to it.",
            status=status,
            volunteer_name=volunteer["name"],
        )
    # superseded
    _enqueue_taken_notice(conn, outreach, shift, volunteer, org)
    filled = conn.execute(
        "SELECT v.name FROM assignments a JOIN volunteers v ON v.id = a.volunteer_id "
        "WHERE a.shift_id = ? AND a.slot_no = ? AND a.status = 'confirmed'",
        (shift["id"], int(workflow["slot_no"])),
    ).fetchone()
    if filled is not None:
        return _fail(
            "already_filled",
            "This shift is already covered -- someone answered just before you. You are not "
            "scheduled for it, and nothing else is needed from you.",
            status=status,
            volunteer_name=volunteer["name"],
            shift_title=shift["title"],
        )
    return _fail(
        "request_withdrawn",
        "This request was withdrawn by the coordinator. You are not scheduled for this shift.",
        status=status,
        volunteer_name=volunteer["name"],
    )


def record_acceptance(token_value: str) -> dict[str, Any]:
    """Verify a volunteer response and, for an acceptance, claim the slot atomically.

    Called from the signed link a volunteer clicks, never by the model. The unique
    partial index on confirmed assignments is what settles two simultaneous yeses.
    """
    try:
        token = tokens.verify(token_value)
    except tokens.TokenError as exc:
        audit.record("response.rejected", actor="volunteer", outcome="denied", detail={"reason": exc.code})
        return _fail(exc.code, exc.message)

    org = policy_mod.load_policy()

    with store.write_tx() as conn:
        outreach = conn.execute(
            "SELECT * FROM outreach WHERE id = ? AND jti = ?", (token.outreach_id, token.jti)
        ).fetchone()
        if outreach is None:
            return _fail("unknown_outreach", "This link is not valid.")
        if outreach["volunteer_id"] != token.volunteer_id:
            return _fail("identity_mismatch", "This link is not valid.")
        workflow = conn.execute("SELECT * FROM workflows WHERE id = ?", (token.workflow_id,)).fetchone()
        shift = conn.execute("SELECT * FROM shifts WHERE id = ?", (token.shift_id,)).fetchone()
        volunteer = conn.execute(
            "SELECT * FROM volunteers WHERE id = ?", (token.volunteer_id,)
        ).fetchone()

        if outreach["status"] != "pending":
            return _closed_outreach_response(conn, outreach, workflow, shift, volunteer, org)

        if int(shift["version"]) != token.shift_version:
            conn.execute(
                "UPDATE outreach SET status = 'superseded', responded_at = ? WHERE id = ?",
                (clock.now_iso(), outreach["id"]),
            )
            return _fail(
                "shift_changed",
                "This shift changed after the request was sent, so the link no longer applies. "
                "The coordinator has been notified.",
            )

        if token.action == "decline":
            conn.execute(
                "UPDATE outreach SET status = 'declined', responded_at = ? WHERE id = ?",
                (clock.now_iso(), outreach["id"]),
            )
            conn.execute(
                "UPDATE workflows SET next_action_at = ? WHERE id = ?",
                (clock.now_iso(), token.workflow_id),
            )
            audit.record(
                "response.declined",
                actor="volunteer",
                workflow_id=token.workflow_id,
                detail={"volunteer_id": token.volunteer_id},
            )
            return {
                "ok": True,
                "action": "decline",
                "message": "Thanks for letting us know. Nothing else is needed.",
                "volunteer_name": volunteer["name"],
                "shift_title": shift["title"],
            }

        # --- acceptance -----------------------------------------------------
        # Eligibility is checked again here, at the moment of the write, because the
        # roster may have changed since the request went out.
        recheck = policy_mod.evaluate_candidates(
            shift["id"],
            cancelling_volunteer_id=None,
            at=clock.now(),
        )
        blocking = [
            exclusion
            for exclusion in recheck.excluded
            if exclusion.volunteer_id == token.volunteer_id
            and set(exclusion.codes) & policy_mod.BLOCKING_AT_ACCEPTANCE
        ]
        if blocking:
            codes = sorted(set(blocking[0].codes) & policy_mod.BLOCKING_AT_ACCEPTANCE)
            conn.execute(
                "UPDATE outreach SET status = 'superseded', responded_at = ? WHERE id = ?",
                (clock.now_iso(), outreach["id"]),
            )
            conn.execute(
                "UPDATE workflows SET next_action_at = ? WHERE id = ?",
                (clock.now_iso(), token.workflow_id),
            )
            audit.record(
                "response.blocked_at_acceptance",
                actor="system",
                outcome="denied",
                workflow_id=token.workflow_id,
                detail={"volunteer_id": token.volunteer_id, "codes": codes},
            )
            return _fail(
                "no_longer_eligible",
                "Thank you for offering. Something changed on the roster, so a coordinator "
                "needs to look at this before you are scheduled.",
                codes=codes,
            )

        try:
            conn.execute(
                """
                INSERT INTO assignments (id, shift_id, volunteer_id, slot_no, status, source, created_at)
                VALUES (?, ?, ?, ?, 'confirmed', 'relay', ?)
                """,
                (
                    ids.new_id("asg"),
                    shift["id"],
                    token.volunteer_id,
                    int(workflow["slot_no"]),
                    clock.now_iso(),
                ),
            )
        except sqlite3.IntegrityError:
            # Someone else's acceptance already claimed this slot.
            conn.execute(
                "UPDATE outreach SET status = 'superseded', responded_at = ? WHERE id = ?",
                (clock.now_iso(), outreach["id"]),
            )
            _enqueue_taken_notice(conn, outreach, shift, volunteer, org)
            audit.record(
                "response.lost_race",
                actor="volunteer",
                workflow_id=token.workflow_id,
                detail={"volunteer_id": token.volunteer_id, "slot_no": int(workflow["slot_no"])},
            )
            return _fail(
                "already_filled",
                "Someone else accepted this shift a moment before you did, so you are not "
                "scheduled for it. Thank you for offering.",
                volunteer_name=volunteer["name"],
                shift_title=shift["title"],
            )

        conn.execute(
            "UPDATE outreach SET status = 'accepted', responded_at = ? WHERE id = ?",
            (clock.now_iso(), outreach["id"]),
        )
        conn.execute(
            """
            UPDATE outreach SET status = 'superseded', responded_at = ?
            WHERE workflow_id = ? AND status = 'pending' AND id != ?
            """,
            (clock.now_iso(), token.workflow_id, outreach["id"]),
        )
        transition(conn, token.workflow_id, states.CONFIRMED, f"{volunteer['name']} accepted")
        conn.execute(
            "UPDATE workflows SET next_action_at = NULL WHERE id = ?", (token.workflow_id,)
        )

        subject, body = messaging.render(
            "coverage_confirmed_volunteer_v1",
            {
                "volunteer_name": volunteer["name"],
                "org_name": org.get("org_name"),
                "shift_title": shift["title"],
                "shift_when": _shift_when(shift),
                "shift_location": shift["location"] or "at the usual location",
            },
        )
        messaging.enqueue(
            idempotency_key=ids.fingerprint(
                {"kind": "confirm", "outreach_id": outreach["id"], "shift_version": token.shift_version}
            ),
            to_email=volunteer["email"],
            subject=subject,
            body=body,
            kind="confirmation",
            workflow_id=token.workflow_id,
            conn=conn,
        )

    audit.record(
        "response.accepted",
        actor="volunteer",
        workflow_id=token.workflow_id,
        detail={
            "volunteer_id": token.volunteer_id,
            "shift_id": token.shift_id,
            "shift_version": token.shift_version,
            "slot_no": int(workflow["slot_no"]),
        },
    )
    write_receipt(token.workflow_id, outcome="confirmed")
    return {
        "ok": True,
        "action": "accept",
        "message": "You are confirmed. A confirmation email is on its way.",
        "volunteer_name": volunteer["name"],
        "shift_title": shift["title"],
        "shift_when": _shift_when(shift),
    }


# ---------------------------------------------------------------------------
# tool 5: escalate_gap
# ---------------------------------------------------------------------------

VALID_OPTION_ACTIONS = {"mark_unresolved", "retry_outreach", "assign_specific_volunteer", "correct_source_data"}


def escalate_gap(
    workflow_id: str,
    *,
    question: str,
    blocker: str,
    summary: str = "",
    options: list[str] | None = None,
) -> dict[str, Any]:
    """Hand one precise decision to a human, with the evidence attached.

    The model writes the question and the summary. It does not invent the options: the
    authorised choices come from this module.
    """
    charge_tool_call(workflow_id, "escalate_gap")
    workflow = get_workflow(workflow_id)
    if workflow["state"] in states.TERMINAL:
        return _fail("workflow_closed", f"workflow is {workflow['state']}")

    open_row = store.query_one(
        "SELECT id FROM escalations WHERE workflow_id = ? AND status = 'open'", (workflow_id,)
    )
    if open_row is not None:
        return {"ok": True, "escalation_id": open_row["id"], "created": False}

    candidates = candidate_set_for(workflow)
    shift = store.query_one("SELECT * FROM shifts WHERE id = ?", (workflow["shift_id"],))
    outreach_rows = store.query(
        """
        SELECT o.volunteer_id, v.name, o.status, o.sent_at, o.expires_at
        FROM outreach o JOIN volunteers v ON v.id = o.volunteer_id
        WHERE o.workflow_id = ? ORDER BY o.sent_at
        """,
        (workflow_id,),
    )

    requested = [action for action in (options or []) if action in VALID_OPTION_ACTIONS]
    allowed = requested or ["retry_outreach", "assign_specific_volunteer", "mark_unresolved"]
    if candidates.eligible:
        allowed = [action for action in allowed if action != "correct_source_data"] or allowed

    evidence = {
        "shift": {
            "id": shift["id"],
            "title": shift["title"],
            "when": _shift_when(shift),
            "required_certification": shift["required_certification"],
            "version": int(shift["version"]),
        },
        "eligible_now": [c.volunteer_id for c in candidates.eligible],
        "exclusions": [
            {"volunteer_id": e.volunteer_id, "name": e.name, "codes": e.codes, "explanation": e.explanation}
            for e in candidates.excluded
        ],
        "outreach": store.rows_to_dicts(outreach_rows),
        "agent_summary": (summary or "")[:1200],
        "policy_version": candidates.policy_version,
    }

    escalation_id = ids.new_id("esc")
    with store.write_tx() as conn:
        conn.execute(
            """
            INSERT INTO escalations (id, workflow_id, created_at, question, blocker, evidence, options, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'open')
            """,
            (
                escalation_id,
                workflow_id,
                clock.now_iso(),
                (question or "").strip()[:500] or "This gap needs a decision.",
                (blocker or "").strip()[:200] or "unspecified",
                store.dumps(evidence),
                store.dumps(allowed),
            ),
        )
        transition(conn, workflow_id, states.NEEDS_HUMAN, (blocker or "needs a human decision")[:200])
        conn.execute("UPDATE workflows SET next_action_at = NULL WHERE id = ?", (workflow_id,))

    audit.record(
        "tool.escalate_gap",
        actor="agent",
        workflow_id=workflow_id,
        detail={"escalation_id": escalation_id, "blocker": blocker, "options": allowed},
    )
    _notify_coordinator(workflow_id, outcome="needs_human", summary=(summary or question or "")[:600])
    return {"ok": True, "escalation_id": escalation_id, "created": True, "options": allowed}


# ---------------------------------------------------------------------------
# tool 6: write_receipt
# ---------------------------------------------------------------------------


def build_receipt(workflow_id: str) -> dict[str, Any]:
    """Assemble the observable record of what happened. No model reasoning included."""
    workflow = get_workflow(workflow_id)
    shift = store.query_one("SELECT * FROM shifts WHERE id = ?", (workflow["shift_id"],))
    event = store.query_one("SELECT * FROM coverage_events WHERE id = ?", (workflow["event_id"],))
    cancelled_by = store.query_one("SELECT name FROM volunteers WHERE id = ?", (event["volunteer_id"],))
    assignment = store.query_one(
        "SELECT a.*, v.name AS volunteer_name FROM assignments a JOIN volunteers v ON v.id = a.volunteer_id "
        "WHERE a.shift_id = ? AND a.slot_no = ? AND a.status = 'confirmed'",
        (workflow["shift_id"], int(workflow["slot_no"])),
    )
    outreach_rows = store.query(
        """
        SELECT o.id, o.volunteer_id, v.name, o.status, o.wave, o.sent_at, o.expires_at,
               o.responded_at, o.rationale, o.outbox_id
        FROM outreach o JOIN volunteers v ON v.id = o.volunteer_id
        WHERE o.workflow_id = ? ORDER BY o.sent_at, o.id
        """,
        (workflow_id,),
    )
    outbox_rows = store.query(
        "SELECT id, to_email, subject, kind, status, provider_message_id, created_at, sent_at "
        "FROM outbox WHERE workflow_id = ? ORDER BY created_at",
        (workflow_id,),
    )
    transitions = store.query(
        "SELECT at, from_state, to_state, reason FROM workflow_transitions WHERE workflow_id = ? ORDER BY id",
        (workflow_id,),
    )
    escalations = store.query(
        "SELECT id, created_at, question, blocker, status, resolution, resolved_by, resolved_at "
        "FROM escalations WHERE workflow_id = ? ORDER BY created_at",
        (workflow_id,),
    )
    exclusions = [
        {"volunteer_id": e.volunteer_id, "name": e.name, "codes": e.codes, "explanation": e.explanation}
        for e in candidate_set_for(workflow).excluded
    ]

    return {
        "workflow_id": workflow_id,
        "generated_at": clock.now_iso(),
        "state": workflow["state"],
        "model_provider": workflow["model_provider"] or "not recorded",
        "counters": {
            "model_calls": int(workflow["model_calls"]),
            "tool_calls": int(workflow["tool_calls"]),
            "emails_queued": int(workflow["emails_sent"]),
        },
        "source_event": {
            "source_event_id": event["source_event_id"],
            "received_at": event["received_at"],
            "cancelled_by": cancelled_by["name"] if cancelled_by else event["volunteer_id"],
            "note_from_volunteer": event["note"],
            "shift_version_at_intake": int(event["shift_version"]),
        },
        "shift": {
            "id": shift["id"],
            "title": shift["title"],
            "when": _shift_when(shift),
            "location": shift["location"],
            "required_certification": shift["required_certification"],
            "version_now": int(shift["version"]),
            "slot_no": int(workflow["slot_no"]),
        },
        "roster_change": (
            {
                "assignment_id": assignment["id"],
                "volunteer": assignment["volunteer_name"],
                "status": assignment["status"],
                "source": assignment["source"],
                "created_at": assignment["created_at"],
            }
            if assignment
            else None
        ),
        "contact_attempts": store.rows_to_dicts(outreach_rows),
        "messages": [audit.redact(dict(row)) for row in outbox_rows],
        "excluded_candidates": exclusions,
        "transitions": store.rows_to_dicts(transitions),
        "escalations": store.rows_to_dicts(escalations),
        "audit": audit.public_for_workflow(workflow_id),
    }


def write_receipt(workflow_id: str, *, outcome: str, summary: str = "") -> dict[str, Any]:
    """Persist the receipt and email the coordinator a link to it."""
    document = build_receipt(workflow_id)
    document["outcome"] = outcome
    document["summary"] = (summary or _default_summary(document))[:1500]

    receipt_id = ids.new_id("rcp")
    existing = store.query_one("SELECT id FROM receipts WHERE workflow_id = ?", (workflow_id,))
    if existing is not None:
        receipt_id = existing["id"]
        store.execute(
            "UPDATE receipts SET created_at = ?, outcome = ?, document = ? WHERE id = ?",
            (clock.now_iso(), outcome, store.dumps(document), receipt_id),
        )
    else:
        store.execute(
            "INSERT INTO receipts (id, workflow_id, created_at, outcome, document) VALUES (?, ?, ?, ?, ?)",
            (receipt_id, workflow_id, clock.now_iso(), outcome, store.dumps(document)),
        )

    audit.record(
        "tool.write_receipt", actor="agent", workflow_id=workflow_id, detail={"receipt_id": receipt_id, "outcome": outcome}
    )
    if outcome == "confirmed":
        _notify_coordinator(workflow_id, outcome=outcome, summary=document["summary"])
    return {"ok": True, "receipt_id": receipt_id, "outcome": outcome, "summary": document["summary"]}


def _default_summary(document: dict[str, Any]) -> str:
    shift = document["shift"]
    if document.get("roster_change"):
        change = document["roster_change"]
        attempts = len(document["contact_attempts"])
        return (
            f"{change['volunteer']} is confirmed for {shift['title']} ({shift['when']}) after "
            f"{attempts} contact attempt(s). The roster was updated at {change['created_at']}."
        )
    return (
        f"{shift['title']} ({shift['when']}) is still uncovered. "
        f"{len(document['contact_attempts'])} contact attempt(s) were made."
    )


OUTCOME_LABELS = {
    "confirmed": "Covered",
    "needs_human": "Needs your decision",
    "unresolved": "Still uncovered",
}


def _notify_coordinator(workflow_id: str, *, outcome: str, summary: str) -> None:
    org = policy_mod.load_policy()
    coordinator_email = org.get("coordinator_email")
    if not coordinator_email or not messaging.allowlisted(coordinator_email):
        return
    workflow = get_workflow(workflow_id)
    shift = store.query_one("SELECT * FROM shifts WHERE id = ?", (workflow["shift_id"],))
    base = get_settings().public_base_url.rstrip("/")
    subject, body = messaging.render(
        "coordinator_receipt_v1",
        {
            "outcome_label": OUTCOME_LABELS.get(outcome, outcome),
            "shift_title": shift["title"],
            "shift_when": _shift_when(shift),
            "summary": summary or "See the receipt for detail.",
            "receipt_url": f"{base}/workflows/{workflow_id}",
        },
    )
    messaging.enqueue(
        idempotency_key=ids.fingerprint({"kind": "coordinator", "workflow_id": workflow_id, "outcome": outcome}),
        to_email=coordinator_email,
        subject=subject,
        body=body,
        kind="coordinator",
        workflow_id=workflow_id,
    )


# ---------------------------------------------------------------------------
# coordinator decisions
# ---------------------------------------------------------------------------


def grant_approval(
    workflow_id: str,
    *,
    action: str,
    params: dict[str, Any],
    granted_by: str,
    ttl_minutes: int = 30,
) -> dict[str, Any]:
    """Bind an approval to exact parameters, the current shift version, and an expiry."""
    workflow = get_workflow(workflow_id)
    shift = store.query_one("SELECT version FROM shifts WHERE id = ?", (workflow["shift_id"],))
    approval_id = ids.new_id("apr")
    store.execute(
        """
        INSERT INTO approvals (id, workflow_id, action, params_fingerprint, shift_version,
                               created_at, expires_at, status, granted_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'granted', ?)
        """,
        (
            approval_id,
            workflow_id,
            action,
            ids.fingerprint(params),
            int(shift["version"]),
            clock.now_iso(),
            clock.iso(clock.now() + _dt.timedelta(minutes=ttl_minutes)),
            granted_by,
        ),
    )
    audit.record(
        "approval.granted",
        actor="coordinator",
        workflow_id=workflow_id,
        detail={"approval_id": approval_id, "action": action, "params": params},
    )
    return {"ok": True, "approval_id": approval_id}


def consume_approval(approval_id: str, *, action: str, params: dict[str, Any]) -> dict[str, Any]:
    """Spend an approval, refusing it if anything it was granted against has changed."""
    row = store.query_one("SELECT * FROM approvals WHERE id = ?", (approval_id,))
    if row is None:
        return _fail("unknown_approval", "that approval does not exist")
    if row["status"] != "granted":
        return _fail("approval_spent", f"approval is {row['status']}")
    if clock.now() > clock.parse(row["expires_at"]):
        store.execute("UPDATE approvals SET status = 'invalidated' WHERE id = ?", (approval_id,))
        return _fail("approval_expired", "that approval has expired; please decide again")
    if row["action"] != action or row["params_fingerprint"] != ids.fingerprint(params):
        audit.record(
            "approval.mismatch",
            actor="system",
            outcome="denied",
            workflow_id=row["workflow_id"],
            detail={"approval_id": approval_id, "expected_action": row["action"], "got_action": action},
        )
        return _fail("approval_mismatch", "that approval was granted for a different action")

    workflow = get_workflow(row["workflow_id"])
    shift = store.query_one("SELECT version FROM shifts WHERE id = ?", (workflow["shift_id"],))
    if int(shift["version"]) != int(row["shift_version"]):
        store.execute("UPDATE approvals SET status = 'invalidated' WHERE id = ?", (approval_id,))
        audit.record(
            "approval.stale",
            actor="system",
            outcome="denied",
            workflow_id=row["workflow_id"],
            detail={"approval_id": approval_id, "approved_version": int(row["shift_version"]), "current_version": int(shift["version"])},
        )
        return _fail(
            "approval_stale",
            "the shift changed after this was approved, so the approval no longer applies",
        )

    store.execute("UPDATE approvals SET status = 'consumed' WHERE id = ?", (approval_id,))
    return {"ok": True, "approval_id": approval_id}


def resolve_escalation(
    escalation_id: str,
    *,
    decision: str,
    resolved_by: str,
    volunteer_id: str | None = None,
) -> dict[str, Any]:
    """Apply a coordinator's decision on an open decision card."""
    row = store.query_one("SELECT * FROM escalations WHERE id = ?", (escalation_id,))
    if row is None:
        return _fail("unknown_escalation", "that decision card does not exist")
    if row["status"] != "open":
        return _fail("already_resolved", "that decision card has already been resolved")

    allowed = store.loads(row["options"]) or []
    if decision not in allowed:
        audit.record(
            "escalation.invalid_decision",
            actor="coordinator",
            outcome="denied",
            workflow_id=row["workflow_id"],
            detail={"escalation_id": escalation_id, "decision": decision, "allowed": allowed},
        )
        return _fail("decision_not_allowed", f"{decision} is not one of the authorised choices", allowed=allowed)

    workflow_id = row["workflow_id"]
    workflow = get_workflow(workflow_id)

    if decision == "assign_specific_volunteer":
        if not volunteer_id:
            return _fail("missing_volunteer", "choose a volunteer to assign")
        approval = grant_approval(
            workflow_id,
            action="assign_specific_volunteer",
            params={"volunteer_id": volunteer_id, "slot_no": int(workflow["slot_no"])},
            granted_by=resolved_by,
        )
        spend = consume_approval(
            approval["approval_id"],
            action="assign_specific_volunteer",
            params={"volunteer_id": volunteer_id, "slot_no": int(workflow["slot_no"])},
        )
        if not spend["ok"]:
            return spend
        outcome = _assign_directly(workflow, volunteer_id, resolved_by)
        if not outcome["ok"]:
            return outcome
        resolution = f"assigned {volunteer_id} by coordinator decision"
    elif decision == "retry_outreach":
        with store.write_tx() as conn:
            conn.execute(
                "UPDATE workflows SET wave = 0, next_action_at = ? WHERE id = ?",
                (clock.now_iso(), workflow_id),
            )
            transition(conn, workflow_id, states.VALIDATED, "coordinator asked Relay to try again")
        resolution = "outreach restarted by coordinator"
    elif decision == "mark_unresolved":
        with store.write_tx() as conn:
            conn.execute("UPDATE workflows SET next_action_at = NULL WHERE id = ?", (workflow_id,))
            transition(conn, workflow_id, states.UNRESOLVED, "coordinator marked the gap unresolved")
        resolution = "left unresolved by coordinator"
    else:  # correct_source_data
        resolution = "coordinator will correct the source record"
        with store.write_tx() as conn:
            conn.execute("UPDATE workflows SET next_action_at = NULL WHERE id = ?", (workflow_id,))
            transition(conn, workflow_id, states.UNRESOLVED, resolution)

    store.execute(
        "UPDATE escalations SET status = 'resolved', resolution = ?, resolved_at = ?, resolved_by = ? WHERE id = ?",
        (resolution, clock.now_iso(), resolved_by, escalation_id),
    )
    audit.record(
        "escalation.resolved",
        actor="coordinator",
        workflow_id=workflow_id,
        detail={"escalation_id": escalation_id, "decision": decision, "volunteer_id": volunteer_id},
    )
    final_state = get_workflow(workflow_id)["state"]
    if final_state in states.TERMINAL:
        write_receipt(workflow_id, outcome="confirmed" if final_state == states.CONFIRMED else "unresolved")
    return {"ok": True, "decision": decision, "resolution": resolution, "state": final_state}


def _assign_directly(workflow: sqlite3.Row, volunteer_id: str, actor: str) -> dict[str, Any]:
    """Coordinator-authorised assignment. Still refuses to break a hard rule silently."""
    shift = store.query_one("SELECT * FROM shifts WHERE id = ?", (workflow["shift_id"],))
    volunteer = store.query_one("SELECT * FROM volunteers WHERE id = ?", (volunteer_id,))
    if volunteer is None:
        return _fail("unknown_volunteer", "that volunteer is not on the roster")

    required = (shift["required_certification"] or "").strip().lower()
    held = [item.strip().lower() for item in (volunteer["certifications"] or "").split(",") if item.strip()]
    if required and required not in held:
        audit.record(
            "assignment.refused",
            actor="system",
            outcome="denied",
            workflow_id=workflow["id"],
            detail={"volunteer_id": volunteer_id, "required": required},
        )
        return _fail(
            "certification_required",
            f"{volunteer['name']} does not hold the organisation-verified '{required}' certification. "
            "Update the volunteer record in the source system first.",
        )

    try:
        with store.write_tx() as conn:
            conn.execute(
                """
                INSERT INTO assignments (id, shift_id, volunteer_id, slot_no, status, source, created_at)
                VALUES (?, ?, ?, ?, 'confirmed', 'relay', ?)
                """,
                (ids.new_id("asg"), shift["id"], volunteer_id, int(workflow["slot_no"]), clock.now_iso()),
            )
            conn.execute(
                "UPDATE outreach SET status = 'superseded', responded_at = ? WHERE workflow_id = ? AND status = 'pending'",
                (clock.now_iso(), workflow["id"]),
            )
            transition(conn, workflow["id"], states.CONFIRMED, f"assigned by {actor}")
            conn.execute("UPDATE workflows SET next_action_at = NULL WHERE id = ?", (workflow["id"],))
    except sqlite3.IntegrityError:
        return _fail("already_filled", "that slot was filled while you were deciding")

    audit.record(
        "assignment.by_coordinator",
        actor="coordinator",
        workflow_id=workflow["id"],
        detail={"volunteer_id": volunteer_id, "slot_no": int(workflow["slot_no"])},
    )
    return {"ok": True}
