"""Ticket #22 -- checkpoint: a full local loop, driven entirely through the
HTTP API on local (mocked) providers, with no network beyond localhost.
"""
from __future__ import annotations

import pytest


@pytest.fixture()
def client(api_client):
    test_client, _engine = api_client
    return test_client


def test_full_local_loop_runs_without_network(client):
    # 1. Create household, member, cook profile, budget
    household = client.post("/api/households", json={"name": "Iyer Household", "default_language": "Tamil"}).json()
    hid = household["id"]

    client.put(f"/api/households/{hid}/cook-profile", json={"name": "Murugan", "language": "Tamil", "skill_level": "expert"})
    client.put(f"/api/households/{hid}/budget", json={"monthly_limit": 5000, "spent_amount": 0, "planned_amount": 0})

    # 2. Add a dish and confirm some inventory via the capture-confirm path
    dish = client.post(
        "/api/dishes",
        json={
            "name": "Paneer Bhurji",
            "ingredients": [{"ingredient": "Paneer", "quantity": 200, "unit": "g"}],
            "prep_minutes": 20,
            "servings": 2,
            "tags": ["routine"],
        },
    ).json()
    assert dish["id"] is not None

    client.post(
        f"/api/households/{hid}/inventory/capture/confirm",
        json={"ingredient": "Rice", "quantity": 2, "unit": "kg", "freshness": "fresh"},
    )

    # 3. Start a loop
    loop = client.post(f"/api/households/{hid}/loops", json={"trigger_type": "manual"}).json()
    loop_id = loop["id"]
    assert loop["status"] == "triggered"

    # 4. Plan: gap calc, price comparison, tier classification, consolidation
    plan = client.post(f"/api/households/{hid}/loops/{loop_id}/plan", json={"servings": 2, "available_minutes": 30}).json()
    assert plan["dish"]["name"] == "Paneer Bhurji"
    assert plan["tier"] in ("green", "yellow", "red")
    assert plan["procurement_path"] in ("top_up_order", "already_stocked", "manual_purchase", "escalated")

    # 5. If approval was required, grant it before executing
    if plan["approval_request_id"]:
        client.post(
            f"/api/households/{hid}/approvals/{plan['approval_request_id']}/decide",
            json={"approved": True, "reason": "within monthly budget"},
        )

    exec_result = client.post(
        f"/api/households/{hid}/loops/{loop_id}/execute-order",
        params={"amount_inr": plan["amount_inr"], "tier": plan["tier"]},
    )
    assert exec_result.status_code == 200

    # 6. Cook brief (register-appropriate, in the cook's language)
    brief = client.post(
        f"/api/households/{hid}/loops/{loop_id}/cook-brief",
        params={"dish_name": "Paneer Bhurji", "instructions": "use less oil than usual"},
    ).json()
    assert brief["language"] == "Tamil"
    assert brief["text"]

    # 7. Cook confirms, eater feedback closes the loop
    client.post(f"/api/households/{hid}/loops/{loop_id}/confirm-cook")
    outcome = client.post(
        f"/api/households/{hid}/loops/{loop_id}/outcome",
        json={"dish_name": "Paneer Bhurji", "rating": 5, "feedback": "great", "consumed": []},
    )
    assert outcome.status_code == 200

    final_loop = client.get(f"/api/households/{hid}/loops/{loop_id}").json()
    assert final_loop["status"] == "completed"
    assert final_loop["cook_confirmed"] is True
    assert final_loop["eater_feedback_captured"] is True

    # 8. Scheduler status is surfaced, not swallowed
    status = client.get("/api/scheduler/status").json()
    assert status["status"] in ("available", "unavailable")


def test_red_order_is_refused_without_approval(client):
    """The refusal beat: a red-tier action must not execute."""
    household = client.post("/api/households", json={"name": "Test HH"}).json()
    hid = household["id"]
    loop = client.post(f"/api/households/{hid}/loops", json={"trigger_type": "manual"}).json()

    resp = client.post(
        f"/api/households/{hid}/loops/{loop['id']}/execute-order",
        params={"amount_inr": 2000, "tier": "red"},
    )
    assert resp.status_code == 403
