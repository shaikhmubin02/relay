"""Eligibility is decided in code, and every refusal can be explained."""

from __future__ import annotations

import datetime as _dt

import pytest

from relay import operations, policy, store

UTC = _dt.timezone.utc


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "spec,start,end,expected",
    [
        ("mon-fri 08:00-14:00", _dt.datetime(2026, 6, 10, 10, 0, tzinfo=UTC), _dt.datetime(2026, 6, 10, 13, 0, tzinfo=UTC), True),
        ("mon-fri 08:00-14:00", _dt.datetime(2026, 6, 10, 13, 0, tzinfo=UTC), _dt.datetime(2026, 6, 10, 16, 0, tzinfo=UTC), False),
        ("mon-fri 08:00-14:00", _dt.datetime(2026, 6, 13, 10, 0, tzinfo=UTC), _dt.datetime(2026, 6, 13, 13, 0, tzinfo=UTC), False),
        ("sat 09:00-13:00;mon-fri 08:00-14:00", _dt.datetime(2026, 6, 13, 9, 0, tzinfo=UTC), _dt.datetime(2026, 6, 13, 12, 0, tzinfo=UTC), True),
        ("", _dt.datetime(2026, 6, 10, 10, 0, tzinfo=UTC), _dt.datetime(2026, 6, 10, 13, 0, tzinfo=UTC), False),
    ],
)
def test_availability_windows(spec, start, end, expected):
    assert policy.covers_shift(spec, start, end) is expected


@pytest.mark.parametrize(
    "spec,at,expected",
    [
        ("21:00-07:00", _dt.datetime(2026, 6, 10, 23, 30, tzinfo=UTC), True),
        ("21:00-07:00", _dt.datetime(2026, 6, 10, 3, 0, tzinfo=UTC), True),
        ("21:00-07:00", _dt.datetime(2026, 6, 10, 8, 10, tzinfo=UTC), False),
        ("06:00-12:00", _dt.datetime(2026, 6, 10, 8, 10, tzinfo=UTC), True),
        ("", _dt.datetime(2026, 6, 10, 8, 10, tzinfo=UTC), False),
    ],
)
def test_quiet_hours_wrap_midnight(spec, at, expected):
    assert policy.in_quiet_hours(spec, at) is expected


# ---------------------------------------------------------------------------
# the fixture roster
# ---------------------------------------------------------------------------


def test_each_exclusion_reason_is_represented(gap):
    """The demo roster exercises every exclusion code Relay can produce."""
    result = operations.eligible_volunteers(gap)
    assert sorted(c["volunteer_id"] for c in result["eligible"]) == ["v_amara", "v_bo", "v_lior"]

    by_id = {e["volunteer_id"]: e["codes"] for e in result["excluded"]}
    assert by_id["v_cal"] == ["missing_certification"]
    assert by_id["v_dara"] == ["not_opted_in"]
    assert by_id["v_eli"] == ["unavailable"]
    assert by_id["v_fen"] == ["already_assigned"]
    assert by_id["v_gita"] == ["contact_cap_reached"]
    assert by_id["v_hugo"] == ["quiet_hours"]
    assert by_id["v_iris"] == ["is_cancelling_volunteer"]
    assert by_id["v_jonah"] == ["inactive"]
    assert by_id["v_kemi"] == ["unavailable"]


def test_every_exclusion_has_a_human_sentence(gap):
    for exclusion in operations.eligible_volunteers(gap)["excluded"]:
        assert exclusion["explanation"].endswith(".")
        assert exclusion["name"] in exclusion["explanation"]
        assert len(exclusion["explanation"]) > len(exclusion["name"]) + 10


def test_a_weekly_cap_of_zero_means_zero(relay):
    """A cap of 0 is a real instruction to pause, not a missing value."""
    row = store.query_one("SELECT max_requests_per_week FROM volunteers WHERE id = 'v_gita'")
    assert int(row["max_requests_per_week"]) == 0
    result = policy.evaluate_candidates("s_packing_am")
    codes = {e.volunteer_id: e.codes for e in result.excluded}
    assert "contact_cap_reached" in codes["v_gita"]


def test_certification_is_not_inferred_from_notes(relay):
    """Cal's note says a course is booked. That is not a certification."""
    result = policy.evaluate_candidates("s_packing_am")
    codes = {e.volunteer_id: e.codes for e in result.excluded}
    assert "missing_certification" in codes["v_cal"]


def test_quiet_hours_do_not_block_an_acceptance(relay):
    """Quiet hours govern whether Relay may ask, not whether a yes counts."""
    assert "quiet_hours" not in policy.BLOCKING_AT_ACCEPTANCE
    assert "contact_cap_reached" not in policy.BLOCKING_AT_ACCEPTANCE
    assert "missing_certification" in policy.BLOCKING_AT_ACCEPTANCE
