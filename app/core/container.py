"""Wires concrete providers into the core interfaces, once per process.

BUILD IT vs SHIP IT is decided here and only here: which concrete classes
get registered. Everything downstream (routes, services) depends only on
the app.core.interfaces protocols and the ToolRegistry, never on a
concrete provider class directly.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.core.registry import ToolKind, ToolRegistry
from app.core.scheduler import LocalScheduler
from app.providers.commerce_mock import CommerceMockProvider
from app.providers.gemini_model import GeminiModelProvider
from app.providers.logistics_mock import MockLogisticsProvider
from app.providers.ollama import OllamaProvider
from app.providers.payments_mock import MockPaymentProvider
from app.providers.recipe_model_chain import RoundRobinRecipeProvider
from app.providers.groq_model import GroqModelProvider
from app.providers.voice import LocalVoiceProvider
from app.providers.zepto_mcp import ZeptoMCPProvider
from app.settings import Settings, get_settings


@dataclass
class Container:
    settings: Settings
    # Kept as the local model used for cook briefs and as the final recipe
    # fallback. Recipe generation itself uses recipe_model_provider below.
    model_provider: OllamaProvider
    recipe_model_provider: RoundRobinRecipeProvider
    registry: ToolRegistry
    scheduler: LocalScheduler
    zepto: ZeptoMCPProvider
    commerce_second: CommerceMockProvider
    payments: MockPaymentProvider
    logistics: MockLogisticsProvider
    voice: LocalVoiceProvider


_container: Container | None = None


def build_container(settings: Settings | None = None) -> Container:
    settings = settings or get_settings()

    model_provider = OllamaProvider(settings.ollama_base_url, settings.ollama_model, settings.request_timeout_seconds)
    recipe_model_provider = RoundRobinRecipeProvider(
        groq=GroqModelProvider(settings.groq_api_key, settings.groq_model, settings.recipe_provider_timeout_seconds),
        gemini=GeminiModelProvider(settings.gemini_api_key, settings.gemini_model, settings.recipe_provider_timeout_seconds),
        fallback=model_provider,
    )
    zepto = ZeptoMCPProvider(settings)
    commerce_second = CommerceMockProvider(provider_name=settings.commerce_second_provider_name)
    payments = MockPaymentProvider()
    logistics = MockLogisticsProvider()
    voice = LocalVoiceProvider(model_provider)

    registry = ToolRegistry()
    registry.register(ToolKind.COMMERCE, zepto)
    registry.register(ToolKind.PAYMENTS, payments)
    registry.register(ToolKind.LOGISTICS, logistics)
    registry.register(ToolKind.VOICE, voice)

    return Container(
        settings=settings,
        model_provider=model_provider,
        recipe_model_provider=recipe_model_provider,
        registry=registry,
        scheduler=LocalScheduler(),
        zepto=zepto,
        commerce_second=commerce_second,
        payments=payments,
        logistics=logistics,
        voice=voice,
    )


def get_container() -> Container:
    global _container
    if _container is None:
        _container = build_container()
    return _container


def reset_container() -> None:
    """Test-only: force a rebuild on next get_container() call."""
    global _container
    _container = None
