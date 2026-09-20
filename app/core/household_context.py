"""The household-memory slice of the planner's model context.

Extracted from `RecipeGenerator._context` so the demo UI can show exactly what
the model was told about a household, built by the same code that tells it.
Two consumers, one definition -- the same reasoning that keeps
`remaining_budget` in one place in `app/services.py`: when the page and the
prompt derive the same fact independently, they drift, and then the page is
making a claim about the system that the system does not honour.

This module deliberately holds **no budget and no inventory**. Money is a
separate channel from the cook (see CLAUDE.md: nothing about spend or approval
state is ever spoken to them), and inventory already has its own endpoint.
Keeping both out is what lets the `/context` response be handed to someone as
"this is the household's memory" without qualification.

A sibling of `recipe_planner.py` and `recipe_briefing.py`, not an extension of
either.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from app.models import CookProfile, HouseholdMember, PreferenceSignal


def signal_is_active(signal: PreferenceSignal, today: date) -> bool:
    """The one definition of preference-signal expiry.

    Both the planner's active list and the UI's expired list derive from this
    predicate, so "expired" on the page and "not sent to the model" cannot
    disagree. A signal with no expiry never expires -- that is the
    identity/constraint case (a cook's skill level, a standing dietary need),
    as opposed to the event case (guests on Saturday) which does.
    """
    return signal.expires_on is None or signal.expires_on >= today


def signal_context(signal: PreferenceSignal) -> dict[str, Any]:
    return {
        "member_id": signal.member_id,
        "signal": signal.signal,
        "sentiment": signal.sentiment,
        "context": signal.context,
        "confidence": signal.confidence,
        "expires_on": signal.expires_on.isoformat() if signal.expires_on else None,
    }


def member_context(members: list[HouseholdMember]) -> list[dict[str, Any]]:
    return [
        {
            "name": member.name,
            "language": member.language,
            "dietary_preferences": member.dietary_preferences,
            "allergies": member.allergies,
            "health_constraints": member.health_constraints,
            "likes": member.likes,
            "dislikes": member.dislikes,
        }
        for member in members
    ]


def cook_context(cook: CookProfile | None) -> dict[str, Any]:
    """Defaults matter here: a household with no cook profile still gets a
    briefing, and the planner has always fallen back to a generic
    intermediate English cook rather than refusing."""
    return {
        "name": cook.name if cook else "Cook",
        "language": cook.language if cook else "English",
        "skill_level": cook.skill_level if cook else "intermediate",
        "available_hours": cook.available_hours if cook else [],
        "confident_dishes": cook.confident_dishes if cook else [],
    }


def partition_preference_signals(
    signals: list[PreferenceSignal], today: date
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split signals into (active, expired).

    `active` is exactly what the model receives, because the planner calls
    this same function to build it. `expired` is what the planner dropped --
    returned rather than discarded so the UI can show a reader the difference
    between "the system knows this" and "the system acted on this".
    """
    active: list[dict[str, Any]] = []
    expired: list[dict[str, Any]] = []
    for signal in signals:
        target = active if signal_is_active(signal, today) else expired
        target.append(signal_context(signal))
    return active, expired
