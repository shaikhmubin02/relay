"""The failure modes Relay is built around.

Each test corresponds to a row in the failure table in docs/ARCHITECTURE.md.
"""

from __future__ import annotations

import threading

from conftest import confirmed_for, link_for, outreach_for, state_of

from relay import faults, messaging, operations, scheduler, states, store, tokens


# ---------------------------------------------------------------------------
# duplicate cancellation
# ---------------------------------------------------------------------------


def test_replaying_a_source_event_creates_one_workflow_and_one_round(relay):
    results = [
        operations.record_cancellation(
            source_event_id="portal-evt-1",
            shift_id="s_packing_am",
            volunteer_id="v_iris",
            note="car trouble",
        )
        for _ in range(3)
    ]
    assert [r["duplicate"] for r in results] == [False, True, True]
    assert len({r["workflow_id"] for r in results}) == 1

    workflow_id = results[0]["workflow_id"]
    scheduler.tick()
    scheduler.tick()

    assert store.query_one("SELECT COUNT(*) AS n FROM workflows")["n"] == 1
    assert store.query_one(
        "SELECT COUNT(*) AS n FROM outreach WHERE workflow_id = ?", (workflow_id,)
    )["n"] == 2
    per_recipient = store.query(
        "SELECT to_email, COUNT(*) AS n FROM outbox GROUP BY to_email HAVING n > 1"
    )
    assert per_recipient == [], "a replayed event must not double-message anyone"


def test_a_cancellation_for_an_unassigned_volunteer_is_rejected(relay):
    result = operations.record_cancellation(
        source_event_id="evt-x", shift_id="s_packing_am", volunteer_id="v_bo"
    )
    assert result["ok"] is False
    assert result["error"] == "no_confirmed_assignment"


# ---------------------------------------------------------------------------
# two acceptances
# ---------------------------------------------------------------------------


def test_two_simultaneous_acceptances_produce_exactly_one_assignment(gap):
    scheduler.tick()
    contacted = list(outreach_for(gap))
    assert len(contacted) == 2

    links = {vid: link_for(gap, vid) for vid in contacted}
    results: dict[str, dict] = {}
    barrier = threading.Barrier(len(links))

    def click(volunteer_id: str, token: str) -> None:
        barrier.wait()
        results[volunteer_id] = operations.record_acceptance(token)
        store.close_connection()

    threads = [threading.Thread(target=click, args=item) for item in links.items()]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    winners = [vid for vid, r in results.items() if r.get("ok")]
    losers = [vid for vid, r in results.items() if not r.get("ok")]
    assert len(winners) == 1
    assert len(losers) == 1
    assert confirmed_for("s_packing_am") == winners
    assert state_of(gap) == states.CONFIRMED


def test_the_volunteer_who_loses_is_told_the_truth(gap):
    scheduler.tick()
    first, second = list(outreach_for(gap))
    assert operations.record_acceptance(link_for(gap, first))["ok"] is True

    losing = operations.record_acceptance(link_for(gap, second))
    assert losing["ok"] is False
    assert losing["error"] == "already_filled"
    assert "already covered" in losing["message"].lower()
    assert "already responded" not in losing["message"].lower()

    messaging.flush()
    notices = [m for m in messaging.inbox() if m["kind"] == "already_covered"]
    assert len(notices) == 1
    assert notices[0]["to_email"].startswith(second.replace("v_", ""))


def test_a_second_click_on_your_own_accepted_link_says_you_are_confirmed(gap):
    scheduler.tick()
    volunteer_id = next(iter(outreach_for(gap)))
    token = link_for(gap, volunteer_id)
    assert operations.record_acceptance(token)["ok"] is True

    again = operations.record_acceptance(token)
    assert again["error"] == "already_accepted"
    assert "confirmed" in again["message"].lower()
    assert len(confirmed_for("s_packing_am")) == 1


# ---------------------------------------------------------------------------
# no response
# ---------------------------------------------------------------------------


def test_silence_moves_to_the_next_wave_then_escalates(gap, relay):
    scheduler.tick()
    assert state_of(gap) == states.AWAITING_RESPONSE
    assert len(outreach_for(gap)) == 2

    relay.advance(minutes=26)
    scheduler.tick()
    statuses = outreach_for(gap)
    assert sum(1 for s in statuses.values() if s == "expired") == 2
    assert "v_lior" in statuses, "the third eligible volunteer should be asked next"
    assert state_of(gap) == states.AWAITING_RESPONSE

    relay.advance(minutes=26)
    scheduler.tick()
    assert state_of(gap) == states.NEEDS_HUMAN
    escalation = store.query_one("SELECT blocker, question FROM escalations WHERE workflow_id = ?", (gap,))
    assert escalation is not None
    assert "asked all 3" in escalation["question"]


def test_nobody_is_contacted_twice_across_waves(gap, relay):
    scheduler.tick()
    relay.advance(minutes=26)
    scheduler.tick()
    counts = store.query(
        "SELECT volunteer_id, COUNT(*) AS n FROM outreach WHERE workflow_id = ? GROUP BY volunteer_id",
        (gap,),
    )
    assert all(int(row["n"]) == 1 for row in counts)


# ---------------------------------------------------------------------------
# declines
# ---------------------------------------------------------------------------


def test_a_decline_frees_relay_to_ask_the_next_person(gap):
    scheduler.tick()
    first = next(iter(outreach_for(gap)))
    result = operations.record_acceptance(link_for(gap, first, action="decline"))
    assert result["ok"] and result["action"] == "decline"
    assert outreach_for(gap)[first] == "declined"
    assert state_of(gap) != states.CONFIRMED


# ---------------------------------------------------------------------------
# unknown delivery
# ---------------------------------------------------------------------------


def test_an_indeterminate_send_escalates_instead_of_resending(gap):
    faults.arm("delivery_unknown", times=1)
    scheduler.tick()

    unknown = store.query("SELECT * FROM outbox WHERE status = 'unknown'")
    assert len(unknown) == 1
    attempts_before = int(unknown[0]["attempts"])

    scheduler.tick()
    scheduler.tick()
    still = store.query_one("SELECT status, attempts FROM outbox WHERE id = ?", (unknown[0]["id"],))
    assert still["status"] == "unknown"
    assert int(still["attempts"]) == attempts_before, "an unknown result must not be retried blindly"

    assert state_of(gap) == states.NEEDS_HUMAN
    escalation = store.query_one("SELECT blocker FROM escalations WHERE workflow_id = ?", (gap,))
    assert escalation["blocker"] == "delivery_unknown"


# ---------------------------------------------------------------------------
# worker restart
# ---------------------------------------------------------------------------


def test_state_survives_a_restart_without_resending(gap):
    scheduler.tick()
    before = {m["idempotency_key"]: m["status"] for m in messaging.inbox()}
    assert before

    # Everything Relay knows is on disk: drop every in-process connection and carry on.
    store.close_connection()

    scheduler.tick()
    after = {m["idempotency_key"]: m["status"] for m in messaging.inbox()}
    assert set(after) == set(before), "a restart must not queue new copies of sent messages"
    assert state_of(gap) == states.AWAITING_RESPONSE


# ---------------------------------------------------------------------------
# model failure
# ---------------------------------------------------------------------------


def test_a_model_failure_leaves_the_gap_open_and_recorded(gap, relay):
    faults.arm("model_error", times=1)
    summary = scheduler.tick()

    assert summary["workflows"][0]["action"] == "agent_failed"
    assert state_of(gap) == states.DETECTED, "a failed run must not fabricate progress"
    errors = store.query(
        "SELECT action FROM audit_log WHERE workflow_id = ? AND outcome = 'error'", (gap,)
    )
    assert any(row["action"] == "agent.run_failed" for row in errors)

    # Backoff, not a hot loop: the retry is scheduled rather than attempted immediately.
    scheduler.tick()
    assert state_of(gap) == states.DETECTED

    relay.advance(minutes=3)
    scheduler.tick()
    assert state_of(gap) == states.AWAITING_RESPONSE, "the gap recovers on its own once backoff elapses"
