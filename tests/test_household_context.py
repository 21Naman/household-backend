"""GET /households/{id}/context — what the model was actually sent.

The endpoint exists so a reader can check a generated recipe against the
household's stated preferences. That check is only worth anything if the page
shows the same signals the planner used, which is why the strongest test here
compares the endpoint's output against the payload embedded in the real prompt
rather than against a hand-written expectation.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlmodel import Session

from app.core.household_context import partition_preference_signals, signal_is_active
from app.core.recipe_planner import RecipeGenerator
from app.core.state_store import LocalStateStore
from app.models import (
    Budget,
    CookProfile,
    Household,
    HouseholdMember,
    InventoryLot,
    MealLoopRecord,
    PreferenceSignal,
)
from app.schemas import RecipeGenerationRequest

UTC = timezone.utc


@pytest.fixture()
def furnished(session: Session) -> int:
    household = Household(name="Context Household", default_language="Hindi")
    session.add(household)
    session.commit()
    session.refresh(household)
    hid = household.id

    session.add(
        HouseholdMember(
            household_id=hid,
            name="Dadi",
            language="Hindi",
            dietary_preferences=["vegetarian"],
            health_constraints=["diabetic"],
        )
    )
    session.add(CookProfile(household_id=hid, name="Asha", language="Hindi", skill_level="beginner"))
    session.add(Budget(household_id=hid, monthly_limit=5000, spent_amount=100, planned_amount=0))
    session.add(InventoryLot(household_id=hid, ingredient="Rice", quantity=900, unit="g"))
    session.add(
        PreferenceSignal(
            household_id=hid,
            signal="guests on Saturday",
            expires_on=date.today() + timedelta(days=3),
        )
    )
    session.add(
        PreferenceSignal(
            household_id=hid,
            signal="Rehana is new to biryani",
            expires_on=None,
        )
    )
    session.add(
        PreferenceSignal(
            household_id=hid,
            signal="exams were last week",
            expires_on=date.today() - timedelta(days=3),
        )
    )
    session.commit()
    return hid


def test_signal_is_active_treats_no_expiry_as_permanent():
    today = date.today()
    permanent = PreferenceSignal(household_id=1, signal="lactose intolerant", expires_on=None)
    future = PreferenceSignal(household_id=1, signal="guests", expires_on=today + timedelta(days=1))
    past = PreferenceSignal(household_id=1, signal="exams", expires_on=today - timedelta(days=1))
    # An expiry falling exactly today is still active: the event has not
    # passed until the day is over.
    boundary = PreferenceSignal(household_id=1, signal="today", expires_on=today)

    assert signal_is_active(permanent, today)
    assert signal_is_active(future, today)
    assert signal_is_active(boundary, today)
    assert not signal_is_active(past, today)


def test_partition_splits_without_losing_a_signal(session, furnished):
    state = LocalStateStore(session).get_household_state(furnished)
    active, expired = partition_preference_signals(state.preference_signals, date.today())

    assert len(active) == 2
    assert len(expired) == 1
    assert len(active) + len(expired) == len(state.preference_signals)
    assert expired[0]["signal"] == "exams were last week"


def test_endpoint_active_signals_are_exactly_what_the_prompt_carries(api_client):
    """The assertion the whole extraction exists for.

    If these two ever diverge, the page is telling a reader the model
    considered something it did not -- which is worse than showing nothing.
    """
    client, engine = api_client
    hid = client.post("/api/households", json={"name": "Prompt Household"}).json()["id"]
    client.put(
        f"/api/households/{hid}/cook-profile",
        json={"name": "Asha", "language": "Hindi", "skill_level": "beginner"},
    )
    client.post(
        f"/api/households/{hid}/preferences",
        json={"signal": "guests on Saturday", "expires_on": str(date.today() + timedelta(days=3))},
    )
    client.post(
        f"/api/households/{hid}/preferences",
        json={"signal": "exams were last week", "expires_on": str(date.today() - timedelta(days=3))},
    )

    response = client.get(f"/api/households/{hid}/context").json()

    with Session(engine) as session:
        state = LocalStateStore(session).get_household_state(hid)
        loop = MealLoopRecord(household_id=hid, trigger_type="manual")
        session.add(loop)
        session.commit()
        session.refresh(loop)
        prompt = RecipeGenerator(None).build_prompt(
            state,
            loop,
            RecipeGenerationRequest(servings=2, guests=0, available_minutes=45),
            servings=2,
            guests=0,
            now=datetime.now(UTC),
        )

    # The context is serialised as one compact line under the marker, so the
    # prompt really is what is asserted against here -- not a re-derived copy.
    marker = "HOUSEHOLD_CONTEXT:\n"
    payload = json.loads(prompt.split(marker, 1)[1].splitlines()[0])

    assert payload["active_preference_signals"] == response["active_preference_signals"]
    assert payload["members"] == response["members"]
    assert payload["cook"] == response["cook"]


def test_expired_signals_are_returned_but_never_sent_to_the_model(api_client):
    client, _ = api_client
    hid = client.post("/api/households", json={"name": "Expiry Household"}).json()["id"]
    client.post(
        f"/api/households/{hid}/preferences",
        json={"signal": "exams were last week", "expires_on": str(date.today() - timedelta(days=3))},
    )

    response = client.get(f"/api/households/{hid}/context").json()

    assert [s["signal"] for s in response["expired_preference_signals"]] == ["exams were last week"]
    assert response["active_preference_signals"] == []


def test_context_never_returns_budget_or_inventory(api_client):
    """Pins the money/cook separation as a test rather than a convention."""
    client, _ = api_client
    hid = client.post("/api/households", json={"name": "Money Household"}).json()["id"]
    client.put(f"/api/households/{hid}/budget", json={"monthly_limit": 5000})
    client.post(
        f"/api/households/{hid}/inventory",
        json={"ingredient": "Rice", "quantity": 900, "unit": "g"},
    )

    response = client.get(f"/api/households/{hid}/context").json()

    assert set(response) == {
        "household",
        "members",
        "cook",
        "active_preference_signals",
        "expired_preference_signals",
    }
    body = json.dumps(response).lower()
    assert "monthly_limit" not in body
    assert "quantity" not in body


def test_context_404s_for_an_unknown_household(api_client):
    client, _ = api_client
    assert client.get("/api/households/9999/context").status_code == 404
