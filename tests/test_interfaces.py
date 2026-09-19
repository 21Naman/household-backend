"""The protocols in app/core/interfaces.py must describe the real providers.

Before this test existed, interfaces.py was imported by nothing and had
quietly gone stale: VoiceProvider.transcribe was missing the
expected_language argument both implementations take, and
PaymentProvider.authorize declared a household_id first argument where the
implementation takes a PineLabsConnection. Documentation nobody executes
stops being documentation.

These are structural (runtime_checkable) checks, so they verify that the
named methods exist on what the container actually wires up. They do not
verify signatures -- that is what the call-site tests elsewhere cover.
"""
from __future__ import annotations

import pytest

from app.core.container import build_container
from app.core.interfaces import (
    CommerceProvider,
    EventScheduler,
    LogisticsProvider,
    ModelProvider,
    PaymentProvider,
    StateStore,
    VoiceProvider,
)
from app.core.state_store import LocalStateStore


@pytest.fixture()
def container():
    return build_container()


@pytest.mark.parametrize(
    "attribute, protocol",
    [
        ("model_provider", ModelProvider),
        ("recipe_model_provider", ModelProvider),
        ("scheduler", EventScheduler),
        ("zepto", CommerceProvider),
        ("commerce_second", CommerceProvider),
        ("payments", PaymentProvider),
        ("logistics", LogisticsProvider),
        ("voice", VoiceProvider),
    ],
)
def test_container_provider_satisfies_its_protocol(container, attribute, protocol):
    provider = getattr(container, attribute)
    assert isinstance(provider, protocol), (
        f"Container.{attribute} ({type(provider).__name__}) no longer satisfies "
        f"{protocol.__name__}; update the provider or the protocol, not neither."
    )


def test_local_state_store_satisfies_state_store(session):
    assert isinstance(LocalStateStore(session), StateStore)


def test_every_registered_tool_is_reachable_through_the_registry(container):
    """The registry is the only path to a tool call, so every provider the
    container builds must actually be registered on it."""
    from app.core.registry import ToolKind

    for kind in ToolKind:
        assert container.registry._providers.get(kind) is not None, f"no provider registered for {kind.value}"
