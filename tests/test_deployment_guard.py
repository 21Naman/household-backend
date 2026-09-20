"""Ticket #7's guard, and the household picker's cross-household read.

The guard tests exist because the original check lived only in `run()`, which
executes under `python -m app.main` and nowhere else. Every hosting platform
starts the app as `uvicorn app.main:app`, importing the module and skipping
`run()` entirely -- so the check that was supposed to stop an unauthenticated
public deployment protected local development and nothing else. These pin the
lifespan version, which every start path reaches.
"""
from __future__ import annotations

import pytest


def test_loopback_with_no_api_key_still_starts(api_client):
    """The default path. Offline boot with zero credentials must always
    succeed -- that is the whole BUILD IT premise, and a guard that broke it
    would be worse than the hole it closes."""
    client, _ = api_client
    assert client.get("/health").status_code == 200


def test_public_deployment_without_a_key_refuses_to_start(api_client_factory):
    with pytest.raises(RuntimeError, match="HOUSEHOLD_API_KEY"):
        with api_client_factory(HOUSEHOLD_PUBLIC_DEPLOYMENT="true"):
            pass


def test_public_deployment_with_a_key_starts(api_client_factory):
    with api_client_factory(
        HOUSEHOLD_PUBLIC_DEPLOYMENT="true", HOUSEHOLD_API_KEY="demo-key"
    ) as (client, _):
        assert client.get("/health").status_code == 200


def test_a_platform_marker_implies_a_public_deployment(api_client_factory, monkeypatch):
    """Hugging Face Spaces sets SPACE_ID in every container. Inferring the
    posture from it means an operator who forgets the explicit flag still
    cannot serve an open database."""
    monkeypatch.setenv("SPACE_ID", "someone/aaj-kya-banega")
    with pytest.raises(RuntimeError, match="HOUSEHOLD_API_KEY"):
        with api_client_factory():
            pass


# ---------------------------------------------------------------------------
# GET /households -- the one deliberate cross-household read
# ---------------------------------------------------------------------------

def test_household_list_exposes_only_id_and_name(api_client):
    """The narrowing is the point. If this ever starts returning the whole
    row, the exception documented in CLAUDE.md has quietly widened."""
    client, _ = api_client
    client.post("/api/households", json={"name": "Alpha", "default_language": "Hindi"})
    client.post("/api/households", json={"name": "Beta", "default_language": "Tamil"})

    rows = client.get("/api/households").json()

    assert [row["name"] for row in rows] == ["Alpha", "Beta"]  # ordered by name
    assert all(set(row) == {"id", "name"} for row in rows)


def test_household_list_requires_the_api_key_when_one_is_configured(api_client_factory):
    """An unauthenticated version of this endpoint is enumeration of every
    household on the box."""
    with api_client_factory(HOUSEHOLD_API_KEY="demo-key") as (client, _):
        assert client.get("/api/households").status_code == 401
        assert client.get("/api/households", headers={"X-API-Key": "demo-key"}).status_code == 200


def test_household_list_is_empty_rather_than_erroring_on_a_fresh_database(api_client):
    client, _ = api_client
    assert client.get("/api/households").json() == []
