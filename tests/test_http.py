"""The HTTP surface: authorisation, and a GET that never changes anything."""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from conftest import confirmed_for, outreach_for

from relay import scheduler, store
from relay.api import app


@pytest.fixture
def client(relay):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def signed_in(client):
    response = client.post("/login", data={"token": "dev-coordinator-token"}, follow_redirects=False)
    assert response.status_code == 303
    return client


def test_the_coordinator_screens_require_a_session(client):
    for path in ("/", "/roster", "/inbox"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/login"


def test_a_wrong_token_is_refused(client):
    response = client.post("/login", data={"token": "guess"}, follow_redirects=False)
    assert response.status_code == 401


def test_state_changing_endpoints_reject_an_unauthenticated_caller(client):
    assert client.post("/demo/seed").status_code == 401
    assert client.post("/demo/tick").status_code == 401
    assert client.post("/workflows/wf_nope/decide", data={"escalation_id": "x", "decision": "y"}).status_code == 401


def test_intake_requires_its_own_token(client):
    body = {"source_event_id": "api-1", "shift_id": "s_packing_am", "volunteer_id": "v_iris"}
    assert client.post("/api/intake/cancellation", json=body).status_code == 401

    response = client.post(
        "/api/intake/cancellation", json=body, headers={"x-relay-intake-token": "dev-intake-token"}
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_a_coordinator_session_does_not_grant_intake(signed_in):
    response = signed_in.post(
        "/api/intake/cancellation",
        json={"source_event_id": "api-2", "shift_id": "s_packing_am", "volunteer_id": "v_iris"},
    )
    assert response.status_code == 401


def test_fetching_a_response_link_does_not_accept_the_shift(signed_in, gap):
    scheduler.tick()
    body = store.query_one(
        "SELECT body FROM outbox WHERE kind = 'outreach' ORDER BY created_at LIMIT 1"
    )["body"]
    token = re.search(r"/r/([A-Za-z0-9_\-.]+)", body).group(1)

    # A mail scanner or link previewer would do exactly this.
    for _ in range(3):
        preview = signed_in.get(f"/r/{token}")
        assert preview.status_code == 200
        assert "Cover this shift?" in preview.text
    assert confirmed_for("s_packing_am") == [], "a GET must never change the rota"

    accepted = signed_in.post(f"/r/{token}")
    assert accepted.status_code == 200
    assert "You are confirmed" in accepted.text
    assert len(confirmed_for("s_packing_am")) == 1


def test_an_invalid_link_gets_a_helpful_page_not_a_stack_trace(client):
    response = client.get("/r/not-a-real-token")
    assert response.status_code == 400
    assert "not valid" in response.text
    assert "Traceback" not in response.text


def test_the_dashboard_shows_a_decision_and_the_receipt_renders(signed_in):
    created = signed_in.post(
        "/demo/scenario", data={"scenario": "no_candidate"}, follow_redirects=False
    )
    assert created.status_code == 303
    workflow_id = created.headers["location"].rsplit("/", 1)[-1]

    dashboard = signed_in.get("/")
    assert "Needs your decision" in dashboard.text

    detail = signed_in.get(f"/workflows/{workflow_id}")
    assert detail.status_code == 200
    assert "forklift" in detail.text
    assert "Who Relay ruled out, and why" in detail.text
    assert "does not hold the organisation-verified certification" in detail.text


def test_applying_a_decision_through_the_form_works(signed_in):
    created = signed_in.post(
        "/demo/scenario", data={"scenario": "no_candidate"}, follow_redirects=False
    )
    workflow_id = created.headers["location"].rsplit("/", 1)[-1]
    escalation = store.query_one("SELECT id FROM escalations WHERE workflow_id = ?", (workflow_id,))

    response = signed_in.post(
        f"/workflows/{workflow_id}/decide",
        data={"escalation_id": escalation["id"], "decision": "mark_unresolved"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert store.query_one("SELECT state FROM workflows WHERE id = ?", (workflow_id,))["state"] == "unresolved"


def test_replaying_the_same_event_through_the_ui_stays_at_one_workflow(signed_in):
    signed_in.post("/demo/replay", data={"scenario": "hero", "times": 3}, follow_redirects=False)
    assert store.query_one("SELECT COUNT(*) AS n FROM workflows")["n"] == 1


def test_healthz_reports_how_the_build_is_configured(client):
    payload = client.get("/healthz").json()
    assert payload["ok"] is True
    assert payload["email_transport"] == "fake"
    assert "demo_clock" in payload
