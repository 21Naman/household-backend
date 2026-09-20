"""GET /households/{id}/audit, and the one trail every refusal lands in.

The endpoint exists because the two behaviours this project is proudest of --
the fail-closed spend gate and the unclosed-loop sweep -- express themselves
almost entirely as audit rows, and nothing could read them.

The refusal tests exist because the three sites that catch ToolCallRefused
each decided separately what to record, and two of them got it wrong.
"""
from __future__ import annotations

import pytest

from app.core.container import get_container
from app.core.registry import ToolKind


def _household(client, name="Audit HH"):
    return client.post("/api/households", json={"name": name}).json()["id"]


def test_audit_feed_returns_newest_first(api_client, red_tier_loop):
    client, _ = api_client
    hid, loop_id, plan = red_tier_loop
    client.post(
        f"/api/households/{hid}/approvals/{plan['approval_request_id']}/decide",
        json={"approved": False, "reason": "too much"},
    )

    events = client.get(f"/api/households/{hid}/audit").json()

    assert events[0]["event"] == "approval_decided", "newest row must come first"
    assert "approved=False" in events[0]["detail"]


def test_audit_feed_is_household_scoped(api_client):
    """There is no cross-household read path here either."""
    client, _ = api_client
    mine = _household(client, "Mine")
    theirs = _household(client, "Theirs")
    loop = client.post(f"/api/households/{theirs}/loops", json={"trigger_type": "manual"}).json()
    client.post(f"/api/households/{theirs}/loops/{loop['id']}/execute-order")

    assert client.get(f"/api/households/{mine}/audit").json() == []
    assert client.get(f"/api/households/{theirs}/audit").json() == []  # 409 writes nothing


def test_audit_feed_caps_an_oversized_limit(api_client, red_tier_loop):
    client, _ = api_client
    hid, _loop_id, _plan = red_tier_loop
    settings = get_container().settings

    events = client.get(f"/api/households/{hid}/audit", params={"limit": 99999}).json()

    assert len(events) <= settings.audit_feed_max_limit


def test_a_refused_execution_leaves_a_readable_row(api_client, red_tier_loop):
    """The refusal is the feature. It has to be visible."""
    client, _ = api_client
    hid, loop_id, _plan = red_tier_loop

    client.post(f"/api/households/{hid}/loops/{loop_id}/execute-order")

    events = client.get(f"/api/households/{hid}/audit").json()
    refusals = [e for e in events if e["event"] == "execution_refused"]
    assert len(refusals) == 1
    assert "not approved" in refusals[0]["detail"]


def test_a_refused_cook_brief_leaves_an_audit_row(api_client):
    """This site used to write nothing at all, so a refused brief vanished.

    The refusal is forced at the registry rather than by contriving a gate
    context, because what is under test is the route's handling of it: before
    the fix this handler caught ToolCallRefused, raised a 403, and recorded
    nothing anywhere.
    """
    from app.core.registry import ToolCallRefused

    client, _ = api_client
    hid = _household(client)
    loop = client.post(f"/api/households/{hid}/loops", json={"trigger_type": "manual"}).json()

    container = get_container()
    original = container.registry.invoke

    def _refuse(kind, method, context, *args, **kwargs):
        if kind is ToolKind.VOICE:
            raise ToolCallRefused(kind, "forced refusal for this test")
        return original(kind, method, context, *args, **kwargs)

    container.registry.invoke = _refuse
    try:
        response = client.post(
            f"/api/households/{hid}/loops/{loop['id']}/cook-brief",
            params={"dish_name": "Khichdi", "instructions": "less oil"},
        )
    finally:
        container.registry.invoke = original

    assert response.status_code == 403
    events = client.get(f"/api/households/{hid}/audit").json()
    refusals = [e for e in events if e["event"] == "tool_call_refused"]
    assert len(refusals) == 1
    assert "forced refusal for this test" in refusals[0]["detail"]


def test_every_refusal_uses_the_event_name_observability_counts(api_client, red_tier_loop):
    """app/providers/observability.py counts blocked actions by event name.

    A refusal filed under any other name is invisible to the weekly
    reflection, which is exactly what was happening to voice-cap refusals.
    """
    client, _ = api_client
    hid, loop_id, _plan = red_tier_loop
    client.post(f"/api/households/{hid}/loops/{loop_id}/execute-order")

    events = client.get(f"/api/households/{hid}/audit").json()
    counted = {"tool_call_refused", "execution_refused"}
    assert any(e["event"] in counted for e in events)
