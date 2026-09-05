"""The agent uses real tools, and never claims more than the tools returned."""

from __future__ import annotations

from conftest import outreach_for, state_of

from relay import operations, scheduler, states, store
from relay.agent import run_agent
from relay.agent.offline_model import PROVIDER_NAME


def test_the_agent_runs_the_expected_tool_sequence(gap):
    run = run_agent(gap)

    assert run.ok, run.error
    assert run.provider == PROVIDER_NAME
    assert run.tool_names() == [
        "load_shift_context",
        "eligible_volunteers",
        "request_coverage",
        "write_receipt",
    ]
    assert all(call["ok"] for call in run.tool_calls)


def test_the_agent_escalates_when_nobody_can_be_asked(relay):
    result = operations.record_cancellation(
        source_event_id="evt-pallet",
        shift_id="s_pallet_pm",
        volunteer_id="v_fen",
        note="Family emergency.",
    )
    workflow_id = result["workflow_id"]
    run = run_agent(workflow_id)

    assert "escalate_gap" in run.tool_names()
    assert "request_coverage" not in run.tool_names()
    assert state_of(workflow_id) == states.NEEDS_HUMAN
    assert store.query_one("SELECT COUNT(*) AS n FROM outbox WHERE kind = 'outreach'")["n"] == 0


def test_sending_a_request_is_never_reported_as_coverage(gap):
    run = run_agent(gap)
    receipt = store.query_one("SELECT outcome, document FROM receipts WHERE workflow_id = ?", (gap,))

    assert receipt["outcome"] == "awaiting_response"
    assert state_of(gap) == states.AWAITING_RESPONSE
    summary = store.loads(receipt["document"])["summary"].lower()
    assert "nobody is scheduled yet" in summary
    assert "confirmed" not in summary
    assert "scheduled" in run.final_text.lower() or "accept" in run.final_text.lower()


def test_the_escalation_question_matches_the_actual_blocker(gap, relay):
    """After two silent waves the reason is silence, not certification."""
    scheduler.tick()
    relay.advance(minutes=26)
    scheduler.tick()
    relay.advance(minutes=26)
    scheduler.tick()

    escalation = store.query_one("SELECT question, evidence FROM escalations WHERE workflow_id = ?", (gap,))
    question = escalation["question"]
    assert "sign-off" not in question, "nobody was blocked by certification here"
    assert "asked all 3" in question
    summary = store.loads(escalation["evidence"])["agent_summary"]
    assert "contacted 3 volunteer(s)" in summary


def test_the_provider_that_actually_ran_is_recorded(gap):
    run_agent(gap)
    row = store.query_one("SELECT model_provider FROM workflows WHERE id = ?", (gap,))
    assert row["model_provider"] == PROVIDER_NAME
    receipt = operations.build_receipt(gap)
    assert receipt["model_provider"] == PROVIDER_NAME


def test_budgets_are_charged_and_visible(gap):
    run_agent(gap)
    row = store.query_one("SELECT model_calls, tool_calls, emails_sent FROM workflows WHERE id = ?", (gap,))
    assert int(row["tool_calls"]) == 4
    assert int(row["model_calls"]) >= 4
    assert int(row["emails_sent"]) == 2


def test_the_tool_budget_is_a_hard_stop(gap, monkeypatch):
    from relay import config

    monkeypatch.setenv("RELAY_MAX_TOOL_CALLS", "2")
    config.reset_settings()

    run = run_agent(gap)
    assert len(outreach_for(gap)) == 0, "the budget stops the run before any message is queued"
    denials = store.query(
        "SELECT action FROM audit_log WHERE workflow_id = ? AND outcome = 'denied'", (gap,)
    )
    assert any(row["action"] in {"tool.budget_exceeded", "hook.tool_cancelled"} for row in denials)
