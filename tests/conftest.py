"""Shared fixtures.

Tests run against a temporary database, a frozen clock and the offline planner, so a
full run needs no network, no credentials and no waiting.
"""

from __future__ import annotations

import datetime as _dt
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from relay import clock, config, faults, operations, seed as seed_mod, store  # noqa: E402

# A Wednesday, 08:10 UTC, one hour fifty before the 10:00 packing shift.
DEMO_NOW = _dt.datetime(2026, 6, 10, 8, 10, tzinfo=_dt.timezone.utc)


@pytest.fixture
def frozen() -> clock.FrozenClock:
    return clock.FrozenClock(DEMO_NOW)


@pytest.fixture
def relay(tmp_path, frozen, monkeypatch):
    """A seeded Relay with a temp database, frozen time and no network."""
    monkeypatch.setenv("RELAY_DB", str(tmp_path / "relay.db"))
    monkeypatch.setenv("RELAY_MODEL_PROVIDER", "offline")
    monkeypatch.setenv("RELAY_EMAIL_TRANSPORT", "fake")
    monkeypatch.setenv("RELAY_TOKEN_SECRET", "test-secret")
    monkeypatch.setenv("RELAY_WORKER_ENABLED", "0")
    config.reset_settings()
    store.close_connection()
    clock.set_clock(frozen)
    faults.disarm()

    seed_mod.seed(demo_clock=False, base=frozen.now())
    yield frozen

    store.close_connection()
    config.reset_settings()
    clock.set_clock(clock.Clock())
    faults.disarm()


@pytest.fixture
def gap(relay):
    """The hero scenario: Iris cancels the 10:00 packing shift."""
    result = operations.record_cancellation(
        source_event_id="evt-hero",
        shift_id="s_packing_am",
        volunteer_id="v_iris",
        note="Car will not start. I could do the 1pm distribution instead if that helps.",
    )
    assert result["ok"] and not result["duplicate"]
    return result["workflow_id"]


def state_of(workflow_id: str) -> str:
    return store.query_one("SELECT state FROM workflows WHERE id = ?", (workflow_id,))["state"]


def confirmed_for(shift_id: str) -> list[str]:
    return [
        row["volunteer_id"]
        for row in store.query(
            "SELECT volunteer_id FROM assignments WHERE shift_id = ? AND status = 'confirmed'",
            (shift_id,),
        )
    ]


def outreach_for(workflow_id: str) -> dict[str, str]:
    return {
        row["volunteer_id"]: row["status"]
        for row in store.query(
            "SELECT volunteer_id, status FROM outreach WHERE workflow_id = ?", (workflow_id,)
        )
    }


def link_for(workflow_id: str, volunteer_id: str, action: str = "accept") -> str:
    from relay import tokens

    row = store.query_one(
        "SELECT * FROM outreach WHERE workflow_id = ? AND volunteer_id = ?",
        (workflow_id, volunteer_id),
    )
    assert row is not None, f"no outreach to {volunteer_id}"
    workflow = store.query_one("SELECT * FROM workflows WHERE id = ?", (workflow_id,))
    shift = store.query_one("SELECT * FROM shifts WHERE id = ?", (workflow["shift_id"],))
    return tokens.issue(
        tokens.ResponseToken(
            jti=row["jti"],
            workflow_id=workflow_id,
            outreach_id=row["id"],
            volunteer_id=volunteer_id,
            shift_id=shift["id"],
            shift_version=int(shift["version"]),
            action=action,
            expires_at=row["expires_at"],
        )
    )
