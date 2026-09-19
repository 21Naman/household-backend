from __future__ import annotations

from datetime import date, timedelta

import pytest


class FakeRecipeProvider:
    """Captures prompts while behaving like the provider-chain protocol."""

    def __init__(self, responses: list[dict], *, provider_name: str = "groq"):
        self.responses = list(responses)
        self.provider_name = provider_name
        self.last_provider_name: str | None = None
        self.prompts: list[str] = []
        self.correction_calls = 0

    def generate_structured(self, prompt: str, schema=None):
        self.prompts.append(prompt)
        self.last_provider_name = self.provider_name
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def generate_correction(self, provider_name: str | None, prompt: str, schema=None):
        self.correction_calls += 1
        return self.generate_structured(prompt, schema)


@pytest.fixture()
def client(api_client_factory):
    """Zepto mock off: this suite exercises pricing through the second
    commerce provider alone, so a Zepto quote must not enter the comparison."""
    with api_client_factory(HOUSEHOLD_ZEPTO_MOCK_ENABLED="false") as (test_client, _engine):
        yield test_client


def _recipe(*, servings: int = 2, prep_minutes: int = 25, ingredients=None) -> dict:
    return {
        "title": "Tomato Dal Rice",
        "servings": servings,
        "prep_minutes": prep_minutes,
        "ingredients": ingredients
        or [
            {"ingredient": "Rice", "quantity": 200, "unit": "g"},
            {"ingredient": "Moong Dal", "quantity": 150, "unit": "g"},
            {"ingredient": "Tomato", "quantity": 2, "unit": "count"},
        ],
        "assumed_pantry_staples": ["water", "salt", "turmeric"],
        "steps": ["Rinse the rice and dal.", "Cook them with tomato and season to taste."],
        "nutrition_notes": ["Dal contributes plant protein and the recipe uses limited added salt."],
    }


def _set_recipe_provider(provider: FakeRecipeProvider) -> None:
    from app.core.container import get_container

    get_container().recipe_model_provider = provider


def _household_with_context(client) -> tuple[int, int]:
    household = client.post("/api/households", json={"name": "Iyer Home", "default_language": "Tamil"}).json()
    household_id = household["id"]
    client.post(
        f"/api/households/{household_id}/members",
        json={
            "name": "Asha",
            "dietary_preferences": ["vegetarian"],
            "allergies": ["peanuts"],
            "health_constraints": ["lower sodium"],
            "likes": ["South Indian food"],
            "dislikes": ["bitter gourd"],
        },
    )
    client.put(
        f"/api/households/{household_id}/cook-profile",
        json={"name": "Murugan", "language": "Tamil", "skill_level": "intermediate", "confident_dishes": ["dal"]},
    )
    client.put(f"/api/households/{household_id}/budget", json={"monthly_limit": 300, "spent_amount": 0, "planned_amount": 0})
    for ingredient, quantity, unit, expiry in (
        ("Rice", 1000, "g", None),
        ("Moong Dal", 500, "g", None),
        ("Milk", 500, "ml", (date.today() - timedelta(days=1)).isoformat()),
    ):
        client.post(
            f"/api/households/{household_id}/inventory",
            json={"ingredient": ingredient, "quantity": quantity, "unit": unit, "freshness": "fresh", "expiry_date": expiry},
        )
    client.post(
        f"/api/households/{household_id}/preferences",
        json={"signal": "avoid oily meals this week", "sentiment": "negative"},
    )
    client.post(
        f"/api/households/{household_id}/history",
        json={"dish_name": "Paneer Bhurji", "accepted": False, "rating": 2, "feedback": "too oily"},
    )
    loop = client.post(
        f"/api/households/{household_id}/loops",
        json={"trigger_type": "manual", "context_note": "quick weeknight dinner", "guest_count": 0},
    ).json()
    return household_id, loop["id"]


def test_recipe_endpoint_returns_fresh_recipe_and_uses_only_usable_inventory(client):
    provider = FakeRecipeProvider([_recipe()])
    _set_recipe_provider(provider)
    household_id, loop_id = _household_with_context(client)

    response = client.post(
        f"/api/v2/households/{household_id}/loops/{loop_id}/recipe",
        json={"servings": 2, "available_minutes": 30, "urgency": "routine"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["recipe"]["title"] == "Tomato Dal Rice"
    assert body["availability_ratio"] == pytest.approx(2 / 3)
    assert body["missing_ingredients"][0]["ingredient"] == "Tomato"
    assert "shopping_gap" not in body  # the duplicate alias was removed
    assert body["quotes"]
    assert body["generation_provider"] == "groq"
    assert client.get(f"/api/households/{household_id}/loops/{loop_id}").json()["status"] == "planned"

    prompt = provider.prompts[0]
    assert '"allergies":["peanuts"]' in prompt
    assert '"ingredient":"Rice"' in prompt
    assert '"ingredient":"Milk","reason":"expired"' in prompt
    assert "avoid oily meals this week" in prompt
    assert "quick weeknight dinner" in prompt


def test_low_stock_recipe_gets_one_correction_and_loop_stays_unplanned_on_failure(client):
    invalid = _recipe(
        ingredients=[
            {"ingredient": "Tomato", "quantity": 2, "unit": "count"},
            {"ingredient": "Onion", "quantity": 2, "unit": "count"},
            {"ingredient": "Spinach", "quantity": 200, "unit": "g"},
        ]
    )
    provider = FakeRecipeProvider([invalid, invalid])
    _set_recipe_provider(provider)
    household_id, loop_id = _household_with_context(client)

    response = client.post(
        f"/api/v2/households/{household_id}/loops/{loop_id}/recipe",
        json={"servings": 2, "available_minutes": 30},
    )
    assert response.status_code == 422
    assert provider.correction_calls == 1
    assert "60%" in response.text
    assert client.get(f"/api/households/{household_id}/loops/{loop_id}").json()["status"] == "triggered"


def test_over_budget_first_recipe_is_corrected_once_before_success(client):
    expensive = _recipe(
        ingredients=[
            {"ingredient": "Rice", "quantity": 200, "unit": "g"},
            {"ingredient": "Moong Dal", "quantity": 150, "unit": "g"},
            {"ingredient": "Tomato", "quantity": 10000, "unit": "count"},
        ]
    )
    provider = FakeRecipeProvider([expensive, _recipe()])
    _set_recipe_provider(provider)
    household_id, loop_id = _household_with_context(client)

    response = client.post(
        f"/api/v2/households/{household_id}/loops/{loop_id}/recipe",
        json={"servings": 2, "available_minutes": 30},
    )
    assert response.status_code == 200, response.text
    assert provider.correction_calls == 1
    assert "cheapest missing-ingredient basket" in provider.prompts[1]


def test_time_violation_gets_one_correction(client):
    provider = FakeRecipeProvider([_recipe(prep_minutes=99), _recipe(prep_minutes=20)])
    _set_recipe_provider(provider)
    household_id, loop_id = _household_with_context(client)

    response = client.post(
        f"/api/v2/households/{household_id}/loops/{loop_id}/recipe",
        json={"servings": 2, "available_minutes": 30},
    )
    assert response.status_code == 200
    assert provider.correction_calls == 1
    assert "must fit within 30 minutes" in provider.prompts[1]


def test_exhausted_provider_chain_leaves_loop_unchanged(client):
    from app.providers.model_failures import RecipeProviderOperationalError

    provider = FakeRecipeProvider([RecipeProviderOperationalError("No recipe-generation provider is currently available")])
    _set_recipe_provider(provider)
    household_id, loop_id = _household_with_context(client)

    response = client.post(
        f"/api/v2/households/{household_id}/loops/{loop_id}/recipe",
        json={"servings": 2, "available_minutes": 30},
    )
    assert response.status_code == 503
    assert client.get(f"/api/households/{household_id}/loops/{loop_id}").json()["status"] == "triggered"
