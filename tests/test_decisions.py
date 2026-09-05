"""Coordinator decisions: bounded options, bound approvals, honest refusals."""

from __future__ import annotations

import datetime as _dt

from conftest import confirmed_for, state_of

from relay import clock, operations, scheduler, states, store


def pallet_gap() -> str:
    result = operations.record_cancellation(
        source_event_id="evt-pallet",
        shift_id="s_pallet_pm",
        volunteer_id="v_fen",
        note="Family emergency.",
    )
    scheduler.tick()
    return result["workflow_id"]


def escalation_of(workflow_id: str):
    return store.query_one("SELECT * FROM escalations WHERE workflow_id = ?", (workflow_id,))


def test_a_decision_outside_the_offered_options_is_refused(relay):
    workflow_id = pallet_gap()
    escalation = escalation_of(workflow_id)

    result = operations.resolve_escalation(
        escalation["id"], decision="email_everyone", resolved_by="coordinator"
    )
    assert result["ok"] is False
    assert result["error"] == "decision_not_allowed"
    assert state_of(workflow_id) == states.NEEDS_HUMAN


def test_marking_a_gap_unresolved_records_it_rather_than_hiding_it(relay):
    workflow_id = pallet_gap()
    escalation = escalation_of(workflow_id)

    result = operations.resolve_escalation(
        escalation["id"], decision="mark_unresolved", resolved_by="coordinator"
    )
    assert result["ok"] and result["state"] == states.UNRESOLVED
    receipt = store.query_one("SELECT outcome FROM receipts WHERE workflow_id = ?", (workflow_id,))
    assert receipt["outcome"] == "unresolved"
    assert confirmed_for("s_pallet_pm") == []


def test_a_coordinator_cannot_assign_someone_without_the_required_signoff(relay):
    workflow_id = pallet_gap()
    escalation = escalation_of(workflow_id)

    result = operations.resolve_escalation(
        escalation["id"],
        decision="assign_specific_volunteer",
        resolved_by="coordinator",
        volunteer_id="v_amara",  # holds food_safety_l1, not forklift
    )
    assert result["ok"] is False
    assert result["error"] == "certification_required"
    assert "source system" in result["message"]
    assert confirmed_for("s_pallet_pm") == []
    assert escalation_of(workflow_id)["status"] == "open", "a refused decision stays open"


def test_a_coordinator_can_assign_someone_who_does_hold_it(relay):
    workflow_id = pallet_gap()
    escalation = escalation_of(workflow_id)

    # The organisation signs Amara off on the pallet jack in its own system.
    store.execute(
        "UPDATE volunteers SET certifications = 'food_safety_l1,forklift' WHERE id = 'v_amara'"
    )
    result = operations.resolve_escalation(
        escalation["id"],
        decision="assign_specific_volunteer",
        resolved_by="coordinator",
        volunteer_id="v_amara",
    )
    assert result["ok"], result
    assert confirmed_for("s_pallet_pm") == ["v_amara"]
    assert state_of(workflow_id) == states.CONFIRMED


def test_retrying_outreach_puts_the_gap_back_in_relays_hands(relay):
    workflow_id = pallet_gap()
    escalation = escalation_of(workflow_id)
    store.execute("UPDATE escalations SET options = ? WHERE id = ?", ('["retry_outreach"]', escalation["id"]))

    result = operations.resolve_escalation(
        escalation["id"], decision="retry_outreach", resolved_by="coordinator"
    )
    assert result["ok"]
    assert state_of(workflow_id) == states.VALIDATED
    assert store.query_one("SELECT next_action_at FROM workflows WHERE id = ?", (workflow_id,))["next_action_at"]


# ---------------------------------------------------------------------------
# approvals
# ---------------------------------------------------------------------------


def test_an_approval_is_bound_to_its_exact_parameters(relay):
    workflow_id = pallet_gap()
    granted = operations.grant_approval(
        workflow_id,
        action="assign_specific_volunteer",
        params={"volunteer_id": "v_amara", "slot_no": 1},
        granted_by="coordinator",
    )
    mismatch = operations.consume_approval(
        granted["approval_id"],
        action="assign_specific_volunteer",
        params={"volunteer_id": "v_bo", "slot_no": 1},
    )
    assert mismatch["error"] == "approval_mismatch"


def test_an_approval_is_void_once_the_shift_changes(relay):
    workflow_id = pallet_gap()
    params = {"volunteer_id": "v_amara", "slot_no": 1}
    granted = operations.grant_approval(
        workflow_id, action="assign_specific_volunteer", params=params, granted_by="coordinator"
    )

    store.execute("UPDATE shifts SET version = version + 1 WHERE id = 's_pallet_pm'")

    result = operations.consume_approval(
        granted["approval_id"], action="assign_specific_volunteer", params=params
    )
    assert result["error"] == "approval_stale"
    assert store.query_one("SELECT status FROM approvals WHERE id = ?", (granted["approval_id"],))["status"] == "invalidated"


def test_an_approval_expires(relay):
    workflow_id = pallet_gap()
    params = {"volunteer_id": "v_amara", "slot_no": 1}
    granted = operations.grant_approval(
        workflow_id,
        action="assign_specific_volunteer",
        params=params,
        granted_by="coordinator",
        ttl_minutes=5,
    )
    relay.advance(minutes=6)
    result = operations.consume_approval(
        granted["approval_id"], action="assign_specific_volunteer", params=params
    )
    assert result["error"] == "approval_expired"


def test_an_approval_cannot_be_spent_twice(relay):
    workflow_id = pallet_gap()
    params = {"volunteer_id": "v_amara", "slot_no": 1}
    granted = operations.grant_approval(
        workflow_id, action="assign_specific_volunteer", params=params, granted_by="coordinator"
    )
    assert operations.consume_approval(
        granted["approval_id"], action="assign_specific_volunteer", params=params
    )["ok"]
    assert operations.consume_approval(
        granted["approval_id"], action="assign_specific_volunteer", params=params
    )["error"] == "approval_spent"
