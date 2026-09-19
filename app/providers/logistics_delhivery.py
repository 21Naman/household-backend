"""Ticket #39 -- Delhivery Maps (live).

Replaces app.providers.logistics_mock.MockLogisticsProvider's ETA/address
calls with a real Delhivery Maps client, behind the exact same
delivery_confidence() contract. Critically: Delhivery supplies address
validation and ETA -- it does NOT supply a confidence score against a
deadline. That derived score (Bible Q5's innovation claim) is computed
here in Python, not fetched from the API, exactly as it is in the mock --
keeping that boundary intact is the whole point of this file existing
separately from a generic Delhivery SDK wrapper.

Requires a live Delhivery Maps API key to exercise; code-complete, not
live-verified in this container. Falls back to the mock's canned ETA at
low confidence on any API failure -- see the watch-out below.
"""
from __future__ import annotations

from datetime import datetime, timezone

import httpx

from app.providers.logistics_mock import DeliveryConfidence, MockLogisticsProvider

UTC = timezone.utc


class DelhiveryError(RuntimeError):
    pass


class DelhiveryLogisticsProvider:
    def __init__(self, api_key: str, base_url: str, timeout_seconds: float = 15.0):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._fallback = MockLogisticsProvider()

    def validate_address(self, address: str) -> dict:
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.get(
                    f"{self.base_url}/geocode",
                    params={"address": address},
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
                response.raise_for_status()
                payload = response.json()
                return {"valid": bool(payload.get("results")), "normalized": payload.get("formatted_address", address), "geocoded": True}
        except httpx.HTTPError as exc:
            raise DelhiveryError(f"Address validation failed: {exc}") from exc

    def delivery_confidence(self, address: str, order_time: datetime, deadline: datetime) -> DeliveryConfidence:
        """Fetches a real ETA from Delhivery Maps, then computes
        confidence the SAME WAY the mock does (available time / typical
        ETA, clamped 0..1) -- Delhivery supplies the ETA input only."""
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.get(
                    f"{self.base_url}/eta",
                    params={"address": address},
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
                response.raise_for_status()
                eta_minutes = float(response.json()["eta_minutes"])
        except (httpx.HTTPError, KeyError, ValueError, TypeError):
            # Fall back to the mock's canned behavior at low confidence,
            # which routes to manual purchase (B008) rather than trusting
            # an unreachable rail -- per Ticket #39's watch-out.
            return self._fallback.delivery_confidence(address, order_time, deadline)

        if order_time.tzinfo is None:
            order_time = order_time.replace(tzinfo=UTC)
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=UTC)
        available_minutes = (deadline - order_time).total_seconds() / 60.0
        confidence = 0.0 if available_minutes <= 0 else min(max(available_minutes / (eta_minutes * 1.5), 0.0), 1.0)

        nearest_shop = None
        if confidence < 0.4:
            nearest_shop = {"name": "Nearest local kirana (fallback)", "distance_km": None}

        return DeliveryConfidence(eta_minutes=eta_minutes, confidence=round(confidence, 3), nearest_shop=nearest_shop)
