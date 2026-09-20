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
from app.core.recipe_audio_cache import RecipeAudioCache
from app.providers.voice import LocalVoiceProvider
from app.providers.voice_gnani import GnaniBackedVoiceProvider, GnaniVoiceProvider
from app.providers.voice_mock import MockVoiceProvider
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
    recipe_audio_cache: RecipeAudioCache


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
    # Cook briefs go through the same Groq/Gemini/Ollama chain as recipe
    # generation and the audio rewrite. The old rule was Ollama-only, which
    # made sense when Ollama was a real process on the same machine; on a
    # cloud deployment with no local model it meant this one endpoint
    # silently degraded to a templated line while every other model path
    # failed over properly. Both providers expose the same
    # generate_structured(prompt, schema) signature.
    voice = _build_voice(settings, recipe_model_provider)

    registry = ToolRegistry(daily_synthesis_limit=settings.gnani_daily_synthesis_limit)
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
        recipe_audio_cache=RecipeAudioCache(
            ttl_seconds=settings.recipe_audio_ttl_seconds,
            max_entries=settings.recipe_audio_cache_max_entries,
        ),
    )


def _build_voice(settings: Settings, model_provider):
    """BUILD IT vs SHIP IT for the voice rail, decided here and only here.

    The offline mock is the default and stays useful after the live rail
    works: it is what CI and credential-free development run against. The
    live rail is selected only when a key is configured AND the mock flag
    is explicitly off, so an operator who sets a key but forgets the flag
    gets the mock plus a startup warning rather than silent live spend.

    Unlike commerce_mock_enabled, pinelabs_mock_enabled and
    delhivery_mock_enabled -- which are reporting-only -- this flag now
    branches.
    """
    if settings.gnani_mock_enabled or not settings.gnani_api_key:
        return MockVoiceProvider(model_provider)
    gnani = GnaniVoiceProvider(
        api_key=settings.gnani_api_key,
        base_url=settings.gnani_base_url,
        tts_model=settings.gnani_tts_model,
        audio_config=settings.gnani_audio_config(),
        speed=settings.gnani_tts_speed,
        timeout_seconds=settings.request_timeout_seconds,
    )
    return GnaniBackedVoiceProvider(model_provider, gnani)


def get_container() -> Container:
    global _container
    if _container is None:
        _container = build_container()
    return _container


def reset_container() -> None:
    """Test-only: force a rebuild on next get_container() call."""
    global _container
    _container = None
