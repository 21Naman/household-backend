"""Core protocols — Ticket #5.

One core, two tracks. BUILD IT (Ollama / SQLite / APScheduler / in-process
tool calls) and SHIP IT (Bedrock / DynamoDB / EventBridge / Lambda) are two
implementations of the same four protocols. Nothing in app/services.py or
app/api/routes.py should import a concrete provider directly — everything
goes through these interfaces, resolved once at startup in
app/core/container.py.

These protocols describe *behaviour* only. The data shapes they exchange
(CartQuote, DeliveryConfidence, PaymentResult, CookBrief, HouseholdState,
ToolKind) are defined once, concretely, next to the code that builds them,
and are imported here rather than re-declared — a second declaration is a
second thing to keep in sync, and it silently went stale the last time.

tests/test_interfaces.py checks every provider the container registers
against the protocol it claims to satisfy, so these definitions cannot
drift away from the implementations again.

Deliberately NOT here: any interface for the deterministic decision
functions in app/services.py (classify_order_tier, compute_ingredient_gap,
consolidate_orders, effective_freshness, remaining_budget). Those are plain
functions, not providers, and they are identical code in both tracks — that
is the whole point of Ticket #13's import-boundary test.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from app.enums import Language, SpendTier
from app.providers.logistics_mock import DeliveryConfidence
from app.providers.payments_mock import PaymentResult
from app.providers.voice import CookBrief
from app.providers.zepto_mcp import CartQuote

__all__ = [
    "CartQuote",
    "Language",
    "CommerceProvider",
    "CookBrief",
    "DeliveryConfidence",
    "EventScheduler",
    "LogisticsProvider",
    "ModelProvider",
    "PaymentProvider",
    "PaymentResult",
    "ProviderHealth",
    "StateStore",
    "VoiceProvider",
]


@runtime_checkable
class ModelProvider(Protocol):
    """Structured language generation: extraction, preference
    explanations, cook dialogue, and recipe *proposals*. A ModelProvider
    never returns a tier, a price, a procurement decision, or authority to
    spend money — those remain deterministic application logic.
    """

    def generate_structured(self, prompt: str, schema: dict | None = None) -> dict[str, Any]:
        """Return a JSON object. If `schema` is given, the response must
        conform to it or a ValueError is raised — never silently coerced."""
        ...

    def health(self) -> "ProviderHealth":
        ...


class ProviderHealth(Protocol):
    status: str
    detail: str | None


@runtime_checkable
class StateStore(Protocol):
    """Household memory. Ticket #16 (local) and Ticket #32 (DynamoDB)
    implement this identically. Every read must be able to report
    staleness against the Bible §4.3 recency window."""

    def get_household_state(self, household_id: int) -> Any:
        ...

    def is_stale(self, household_id: int, max_age_seconds: int) -> bool:
        ...


@runtime_checkable
class EventScheduler(Protocol):
    """Ticket #17 (APScheduler) and Ticket #35 (EventBridge) implement
    this identically. Three trigger classes: scheduled, reactive, manual."""

    def register_daily_trigger(self, household_id: int, hour: int = 9) -> None:
        ...

    def register_sweep(self, name: str, interval_seconds: int, callback) -> None:
        ...

    def emit_reactive(self, household_id: int, event_type: str, payload: dict) -> None:
        ...

    def status(self) -> tuple[str, str | None]:
        ...


@runtime_checkable
class VoiceProvider(Protocol):
    """Both legs of the cook interface.

    `transcribe` remains declared although no implementation currently
    serves it: the local Whisper adapter was removed once Gnani credentials
    existed, and Gnani's own STT stays gated on RQ7. Two-way voice is
    deferred, not abandoned, so the seat stays declared rather than being
    deleted and reinstated later as a protocol change.

    `synthesize` is deliberately generic — text in, audio bytes out, with
    nothing recipe-shaped about it. Turning a recipe into speakable
    sentences is a separate model call that happens in
    app/core/recipe_briefing.py before this is ever reached.
    """

    def transcribe(self, audio: bytes, suffix: str = ".webm", expected_language: str | None = None) -> dict:
        ...

    def reply(self, dish_name: str, instructions: str, language: str, skill_level: str) -> CookBrief:
        ...

    def synthesize(self, text: str, language: Language, voice: str | None = None) -> bytes:
        ...

    def audio_media_type(self) -> str:
        """The media type of whatever `synthesize` returns, so the route
        does not have to assume. The live rail is configurable (MP3 by
        default); the offline mock answers with what it can actually
        generate."""
        ...


@runtime_checkable
class LogisticsProvider(Protocol):
    def validate_address(self, address: str) -> dict:
        ...

    def delivery_confidence(self, address: str, order_time: datetime, deadline: datetime) -> DeliveryConfidence:
        ...


@runtime_checkable
class PaymentProvider(Protocol):
    """Reserve-ceiling-once, debit-against-ceiling — the Pine Labs P3P
    model (Bible §3.1 correction). Receives a tier; never computes one.

    `connection` carries the household's reserved ceiling. It is the first
    positional argument because the ceiling, not the household id, is what
    the authorization is actually checked against.
    """

    def authorize(self, connection: Any, amount_inr: float, tier: SpendTier) -> PaymentResult:
        ...


@runtime_checkable
class CommerceProvider(Protocol):
    """Read paths (search/quote_cart) are cheap and safe; place_order is
    not, and reaches a provider only through the gated ToolRegistry."""

    def search(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        ...

    def quote_cart(self, *args: Any, **kwargs: Any) -> CartQuote:
        ...

    def place_order(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        ...
