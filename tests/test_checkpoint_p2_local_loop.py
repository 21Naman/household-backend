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

    # No amount and no tier are sent: the server reads the basket it priced
    # onto the loop during the plan step above.
    exec_result = client.post(f"/api/households/{hid}/loops/{loop_id}/execute-order")
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


def test_red_order_is_refused_without_approval(client, red_tier_loop):
    """The refusal beat: a red-tier action must not execute unapproved.

    The previous version of this test created a bare household with no plan,
    no quote and no approval, and asserted 403 by passing `tier=red` as a
    query parameter -- which proved only that the endpoint fed the caller's
    own input back into the gate. It passed just as happily against the
    vulnerability it was supposed to be covering. The tier is now derived by
    classify_order_tier from a basket that genuinely prices above the red
    floor.
    """
    hid, loop_id, plan = red_tier_loop

    assert plan["tier"] == "red"
    assert plan["approval_request_id"] is not None, "a red basket must park for approval"

    resp = client.post(f"/api/households/{hid}/loops/{loop_id}/execute-order")

    assert resp.status_code == 403
    assert "not approved" in resp.json()["detail"]


def test_a_client_cannot_talk_its_way_past_the_gate_with_query_parameters(client, red_tier_loop):
    """The regression test for the actual defect.

    `?tier=green` used to make check_execution_authorized return early on
    "green tier executes without approval" without ever looking for an
    approval row. The parameters are now ignored entirely, so a caller
    sending them gets exactly the refusal one who omits them gets.
    """
    hid, loop_id, _plan = red_tier_loop

    honest = client.post(f"/api/households/{hid}/loops/{loop_id}/execute-order")
    tampered = client.post(
        f"/api/households/{hid}/loops/{loop_id}/execute-order",
        params={"amount_inr": 1, "tier": "green"},
    )

    assert tampered.status_code == 403
    assert tampered.json()["detail"] == honest.json()["detail"]


def test_an_approved_red_order_reaches_the_payment_rail_and_is_told_to_use_a_human(
    client, red_tier_loop
):
    """Approval has to actually lead somewhere.

    Until the registry's payment gate learned about verified human approval,
    an approved red order passed check_execution_authorized and was then
    refused outright by the gate for not being green -- the approval path
    dead-ended before anything could even consider the payment.

    It now reaches the rail, which makes its own separate decision: the
    reserved-ceiling mock only debits unattended within a pre-authorised
    green ceiling, so it answers `requires_human` with a reason rather than
    executing. That is the honest outcome, and it is a different thing from
    being blocked at the gate -- this is the system consulting the rail and
    reporting what it said.
    """
    hid, loop_id, plan = red_tier_loop
    client.post(
        f"/api/households/{hid}/approvals/{plan['approval_request_id']}/decide",
        json={"approved": True, "reason": "checked and fine"},
    )

    resp = client.post(f"/api/households/{hid}/loops/{loop_id}/execute-order")

    assert resp.status_code == 200
    body = resp.json()
    assert body["requires_human"] is True
    assert body["executed"] is False
    assert "requires human approval before any payment call" in body["reason"]


def test_a_stale_approval_is_refused_when_the_basket_changes(client, red_tier_loop):
    """Approval is for a basket, not for a loop.

    Some of the missing ingredient turning up in the kitchen shrinks the
    basket, so re-planning the same loop moves quoted_amount_inr while the
    approval keeps the snapshot it was granted against. The gate refuses
    rather than spending against a figure nobody agreed to.
    """
    hid, loop_id, plan = red_tier_loop
    client.post(
        f"/api/households/{hid}/approvals/{plan['approval_request_id']}/decide",
        json={"approved": True, "reason": "checked and fine"},
    )

    client.post(
        f"/api/households/{hid}/inventory",
        json={"ingredient": "Saffron", "quantity": 6000, "unit": "g", "freshness": "fresh"},
    )
    replanned = client.post(
        f"/api/households/{hid}/loops/{loop_id}/plan",
        json={"servings": 2, "available_minutes": 30},
    ).json()
    assert replanned["amount_inr"] != plan["amount_inr"]

    resp = client.post(f"/api/households/{hid}/loops/{loop_id}/execute-order")

    assert resp.status_code == 403
    assert "basket changed since approval" in resp.json()["detail"]


def test_executing_a_loop_that_was_never_planned_is_refused(client):
    """A missing basket must not read as a free order."""
    hid = client.post("/api/households", json={"name": "Unplanned HH"}).json()["id"]
    loop = client.post(f"/api/households/{hid}/loops", json={"trigger_type": "manual"}).json()

    resp = client.post(f"/api/households/{hid}/loops/{loop['id']}/execute-order")

    assert resp.status_code == 409
    assert "no priced basket" in resp.json()["detail"]
