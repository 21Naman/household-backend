"""Ticket #19 — mock logistics / delivery-confidence provider.

Delhivery Maps supplies address validation, routing, and ETA -- it does
NOT supply a confidence score against a real deadline ("the cook starts in
25 minutes"). That derived confidence score is the team's own layer on top
(Bible Q5's innovation claim), and this mock is where that boundary is
drawn explicitly: `delivery_confidence()` computes the score; a live
Delhivery client (Ticket #39) would only ever supply the ETA input to it.

Confidence is a plain number app.services.consolidate_orders thresholds
on -- never prose, and never computed by a model.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

UTC = timezone.utc


@dataclass
class DeliveryConfidence:
    eta_minutes: float
    confidence: float  # 0..1
    nearest_shop: dict | None = None


class MockLogisticsProvider:
    def __init__(self, base_eta_minutes: float = 25.0):
        self.base_eta_minutes = base_eta_minutes

    def validate_address(self, address: str) -> dict:
        return {"valid": bool(address and address.strip()), "normalized": address.strip(), "geocoded": True}

    def delivery_confidence(self, address: str, order_time: datetime, deadline: datetime) -> DeliveryConfidence:
        """Confidence formula: the ratio of (time available before the
        deadline) to (typical delivery ETA), clamped to [0, 1]. Below 1.0
        the delivery is cutting it close; below the caller's threshold
        (app.services default 0.4) it should not be trusted at all -- B008's
        manual-purchase fallback exists exactly for this case."""
        if order_time.tzinfo is None:
            order_time = order_time.replace(tzinfo=UTC)
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=UTC)

        available_minutes = (deadline - order_time).total_seconds() / 60.0
        if available_minutes <= 0:
            confidence = 0.0
        else:
            confidence = min(available_minutes / (self.base_eta_minutes * 1.5), 1.0)
            confidence = max(confidence, 0.0)

        nearest_shop = None
        if confidence < 0.4:
            nearest_shop = {"name": "Nearest local kirana (fallback)", "distance_km": 0.8}

        return DeliveryConfidence(eta_minutes=self.base_eta_minutes, confidence=round(confidence, 3), nearest_shop=nearest_shop)
