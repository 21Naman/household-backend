"""Smoke coverage for the SHIP IT Lambda handlers.

lambdas/handlers.py had no test of any kind, which mattered because two of
its handlers duplicate behaviour that also exists in the FastAPI app:

  * scheduled_trigger_handler duplicates app.core.scheduler.scheduled_trigger,
    but adds a per-household per-day idempotency check the app version does
    not have (Ticket #35's watch-out: a double-firing EventBridge rule must
    not create two competing loops for one dinner).
  * reflection_handler duplicates the weekly-reflection route with a
    different output shape.

These tests pin the behaviour that actually differs, so the two copies
cannot silently drift apart unnoticed. They do not require AWS: every
handler is invoked directly with a plain event dict.
"""
from __future__ import annotations

import json

import pytest

from lambdas import handlers


@pytest.fixture()
def lambda_engine(monkeypatch, tmp_path):
    """Point the handlers at a throwaway database.

    Patching app.database.engine alone is not enough: modules that do
    `from app.database import engine` bind the object at import time and keep
    using it, so each of those bindings has to be redirected too or the
    handler quietly operates on whatever database an earlier test left behind.
    """
    import app.core.unclosed_sweep as sweep_module
    import app.database as database_module
    from sqlmodel import SQLModel, create_engine

    engine = create_engine(f"sqlite:///{tmp_path / 'lambda.db'}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(database_module, "engine", engine)
    monkeypatch.setattr(sweep_module, "engine", engine)
    return engine


def _body(response: dict) -> dict:
    return json.loads(response["body"])


def test_commerce_handler_quotes_and_searches():
    quote = handlers.commerce_handler({"items": [{"ingredient": "Tomato", "quantity": 200, "unit": "g"}]}, None)
    assert quote["statusCode"] == 200
    assert _body(quote)["total_inr"] > 0

    found = handlers.commerce_handler({"action": "search", "query": "tomato"}, None)
    assert _body(found)["results"][0]["name"] == "Tomato"


def test_payments_handler_refuses_a_non_green_tier():
    response = handlers.payments_handler({"amount_inr": 100, "tier": "red"}, None)
    assert _body(response)["executed"] is False
    assert _body(response)["requires_human"] is True


def test_payments_handler_refuses_to_go_live_while_pine_labs_is_blocked(monkeypatch):
    monkeypatch.setenv("PINELABS_LIVE", "true")
    response = handlers.payments_handler({"amount_inr": 100, "tier": "green"}, None)
    assert response["statusCode"] == 500
    assert "BLOCKED" in _body(response)["error"]


def test_logistics_handler_scores_delivery_confidence():
    response = handlers.logistics_handler(
        {
            "address": "12 MG Road",
            "order_time": "2026-06-01T18:00:00+00:00",
            "deadline": "2026-06-01T18:40:00+00:00",
        },
        None,
    )
    assert response["statusCode"] == 200
    assert 0.0 <= _body(response)["confidence"] <= 1.0


def test_voice_handler_falls_back_to_text_without_a_model_provider():
    response = handlers.voice_handler(
        {"dish_name": "Dal", "instructions": "use less salt", "language": "Tamil", "skill_level": "intermediate"},
        None,
    )
    assert response["statusCode"] == 200
    assert "Dal" in _body(response)["text"]


def test_scheduled_trigger_handler_requires_a_household_id():
    assert handlers.scheduled_trigger_handler({}, None)["statusCode"] == 400


def test_scheduled_trigger_handler_is_idempotent_within_a_day(lambda_engine):
    """The behaviour that distinguishes this handler from the in-process
    scheduler: firing twice for one household on one day makes one loop."""
    from sqlmodel import Session, select

    from app.models import Household, MealLoopRecord

    with Session(lambda_engine) as session:
        household = Household(name="Lambda Household")
        session.add(household)
        session.commit()
        session.refresh(household)
        household_id = household.id

    first = handlers.scheduled_trigger_handler({"household_id": household_id}, None)
    assert first["statusCode"] == 201

    second = handlers.scheduled_trigger_handler({"household_id": household_id}, None)
    assert second["statusCode"] == 200
    assert _body(second)["skipped"] is True

    with Session(lambda_engine) as session:
        loops = list(session.exec(select(MealLoopRecord).where(MealLoopRecord.household_id == household_id)))
    assert len(loops) == 1


def test_unclosed_sweep_handler_reports_what_it_flagged(lambda_engine):
    response = handlers.unclosed_sweep_handler({}, None)
    assert response["statusCode"] == 200
    assert _body(response)["flagged"] == []


def test_reflection_handler_returns_one_reflection_per_household(lambda_engine):
    from sqlmodel import Session

    from app.models import Household

    with Session(lambda_engine) as session:
        session.add(Household(name="Reflecting Household"))
        session.commit()

    response = handlers.reflection_handler({}, None)
    assert response["statusCode"] == 200
    assert len(_body(response)["reflections"]) == 1
