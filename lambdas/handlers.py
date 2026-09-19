"""Ticket #34 -- Lambda tool execution.

One handler per tool (voice, logistics, payments, commerce), each meant to
run under its OWN IAM role granting access to exactly one external API and
its one secret (see infra/cdk/tools_stack.py). The gating decision was
already made before the invocation reached here -- app.core.registry's
gate runs in the FastAPI process, not in the Lambda; these handlers
execute, they do not decide. A shared "do everything" role would
undermine that isolation, so keep these four handlers structurally
separate even though the bodies are short.

Cold start can delay a time-sensitive order -- app.providers.logistics_mock
/logistics_delhivery's confidence score already accounts for elapsed time
before the cook's start, so a slow cold start degrades into a lower
confidence score (and possibly a manual-purchase recommendation) rather
than a silently late delivery.
"""
from __future__ import annotations

import json
import os
from typing import Any


def _response(status_code: int, body: dict) -> dict:
    return {"statusCode": status_code, "body": json.dumps(body)}


def voice_handler(event: dict, context: Any) -> dict:
    """Invoked for the cook-brief reply leg. Expects
    {"dish_name", "instructions", "language", "skill_level"} in the event
    body, already gated by app.core.registry before this was ever queued."""
    from app.providers.voice import LocalVoiceProvider  # placeholder model provider path

    payload = json.loads(event.get("body", "{}")) if "body" in event else event
    try:
        # In a real deployment this would construct BedrockModelProvider;
        # left generic here so the handler is testable without live AWS.
        model_provider = context.model_provider if hasattr(context, "model_provider") else None
        provider = LocalVoiceProvider(model_provider)
        brief = provider.reply(
            dish_name=payload["dish_name"],
            instructions=payload["instructions"],
            language=payload["language"],
            skill_level=payload["skill_level"],
        )
        return _response(200, {"text": brief.text, "language": brief.language})
    except Exception as exc:
        return _response(500, {"error": str(exc)})


def logistics_handler(event: dict, context: Any) -> dict:
    """Invoked for delivery-confidence checks. Ticket #39 swaps the mock
    for live Delhivery Maps behind the same contract."""
    from datetime import datetime

    from app.providers.logistics_mock import MockLogisticsProvider

    payload = json.loads(event.get("body", "{}")) if "body" in event else event
    try:
        use_live = os.environ.get("DELHIVERY_LIVE", "false").lower() == "true"
        if use_live:
            from app.providers.logistics_delhivery import DelhiveryLogisticsProvider

            provider = DelhiveryLogisticsProvider(api_key=os.environ["DELHIVERY_API_KEY"], base_url=os.environ["DELHIVERY_MAPS_BASE_URL"])
        else:
            provider = MockLogisticsProvider()
        result = provider.delivery_confidence(
            address=payload["address"],
            order_time=datetime.fromisoformat(payload["order_time"]),
            deadline=datetime.fromisoformat(payload["deadline"]),
        )
        return _response(200, {"eta_minutes": result.eta_minutes, "confidence": result.confidence, "nearest_shop": result.nearest_shop})
    except Exception as exc:
        return _response(500, {"error": str(exc)})


def payments_handler(event: dict, context: Any) -> dict:
    """Invoked ONLY for green-tier authorization -- app.core.registry's
    gate already refused anything else before this Lambda would ever be
    invoked. Ticket #40 (BLOCKED on RQ4/RQ5) swaps the mock for live Pine
    Labs P3P behind the same contract once those research questions are
    answered."""
    from app.enums import SpendTier
    from app.providers.payments_mock import MockPaymentProvider

    payload = json.loads(event.get("body", "{}")) if "body" in event else event
    try:
        use_live = os.environ.get("PINELABS_LIVE", "false").lower() == "true"
        if use_live:
            from app.providers.payments_pinelabs import PineLabsNotYetValidated

            raise PineLabsNotYetValidated(
                "Live Pine Labs is BLOCKED on RQ4/RQ5 per the build map -- "
                "do not set PINELABS_LIVE=true until the sandbox validates the household-ceiling mandate shape."
            )
        provider = MockPaymentProvider()
        # The event carries the tier as a JSON string. It has to become a
        # SpendTier before it reaches the provider: the refusal path formats
        # tier.value, so a raw string turned every non-green authorization
        # into a 500 rather than the clean "requires human" refusal it is.
        tier = SpendTier(payload["tier"])
        # `connection` would normally be loaded by household_id; left as
        # None here to keep the handler self-contained for unit testing.
        result = provider.authorize(None, payload["amount_inr"], tier)
        return _response(200, {"executed": result.executed, "requires_human": result.requires_human, "reason": result.reason})
    except Exception as exc:
        return _response(500, {"error": str(exc)})


def commerce_handler(event: dict, context: Any) -> dict:
    """Invoked for cart quoting / search across providers."""
    from app.providers.commerce_mock import CommerceMockProvider

    payload = json.loads(event.get("body", "{}")) if "body" in event else event
    try:
        provider = CommerceMockProvider()
        if payload.get("action") == "search":
            return _response(200, {"results": provider.search(payload["query"])})
        quote = provider.quote_cart(payload.get("items", []))
        return _response(200, {"provider": quote.provider_name, "total_inr": quote.total_inr, "items": quote.items})
    except Exception as exc:
        return _response(500, {"error": str(exc)})


def scheduled_trigger_handler(event: dict, context: Any) -> dict:
    """EventBridge target for the daily check-in. Idempotent per
    household per day -- see the Watch-out in Ticket #35: a misfiring or
    double-firing rule must not create two competing loops for one
    dinner."""
    from datetime import date, timezone

    from sqlmodel import Session, select

    from app.database import engine
    from app.enums import LoopStatus
    from app.models import MealLoopRecord

    household_id = event.get("household_id") or json.loads(event.get("detail", "{}")).get("household_id")
    if household_id is None:
        return _response(400, {"error": "household_id is required"})

    today = date.today()
    with Session(engine) as session:
        existing = list(
            session.exec(
                select(MealLoopRecord)
                .where(MealLoopRecord.household_id == household_id)
                .where(MealLoopRecord.trigger_type == "scheduled")
            )
        )
        already_today = any(loop.created_at.date() == today for loop in existing)
        if already_today:
            return _response(200, {"skipped": True, "reason": "already triggered today (idempotency)"})

        loop = MealLoopRecord(household_id=household_id, trigger_type="scheduled", status=LoopStatus.TRIGGERED)
        session.add(loop)
        session.commit()
        session.refresh(loop)
        return _response(201, {"loop_id": loop.id})


def unclosed_sweep_handler(event: dict, context: Any) -> dict:
    from app.core.unclosed_sweep import run_unclosed_sweep

    timeout_hours = int(os.environ.get("LOOP_UNCLOSED_TIMEOUT_HOURS", "6"))
    flagged = run_unclosed_sweep(timeout_hours)
    return _response(200, {"flagged": flagged})


def reflection_handler(event: dict, context: Any) -> dict:
    """Weekly reflection Lambda. Must run every week without skipping --
    a missing reflection is indistinguishable from a clean one."""
    from datetime import datetime, timedelta, timezone

    from sqlmodel import Session, select

    from app.database import engine
    from app.models import AuditEvent, Household, MealLoopRecord
    from app.providers.observability import build_weekly_reflection

    since = datetime.now(timezone.utc) - timedelta(days=7)
    results = []
    with Session(engine) as session:
        households = list(session.exec(select(Household)))
        for household in households:
            events = list(
                session.exec(
                    select(AuditEvent).where(AuditEvent.household_id == household.id).where(AuditEvent.created_at >= since)
                )
            )
            loops = list(
                session.exec(
                    select(MealLoopRecord)
                    .where(MealLoopRecord.household_id == household.id)
                    .where(MealLoopRecord.created_at >= since)
                )
            )
            reflection = build_weekly_reflection(household.id, events, loops)
            results.append(reflection.__dict__)

    return _response(200, {"reflections": results})
