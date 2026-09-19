"""Ticket #20 — a second (mocked) quick-commerce provider.

B015/B020 require comparing prices across apps before consolidating an
order. The original codebase only integrated Zepto, so
app.services.consolidate_orders had nothing to compare against. This mock
satisfies the same shape as ZeptoMCPProvider's CommerceProvider methods so
either can be swapped for a live second provider (Blinkit, Instamart, ...)
later without touching the comparison logic itself.

Deliberately a plain mock, not OAuth-backed: the point of this ticket is
proving the *comparison* is real and deterministic, not standing up a
second full rail integration.
"""
from __future__ import annotations

import hashlib
from typing import Any

from app.providers.zepto_mcp import CartQuote


class CommerceMockProvider:
    def __init__(self, provider_name: str = "Blinkit", price_variance: float = 0.15):
        self.provider_name = provider_name
        # Deterministic per-ingredient variance (not random) so tests are
        # reproducible: a hash of the ingredient name nudges price up/down
        # within +/- price_variance, which is enough to make "pick the
        # cheaper one" a meaningful, non-trivial comparison in tests.
        self._price_variance = price_variance

    def _base_price(self, ingredient: str) -> float:
        # ~₹0.20/g-equivalent baseline, nudged deterministically per item.
        digest = int(hashlib.sha256(ingredient.lower().encode()).hexdigest(), 16)
        variance = ((digest % 1000) / 1000.0 - 0.5) * 2 * self._price_variance
        return round(20.0 * (1 + variance), 2)

    def search(self, query: str) -> list[dict[str, Any]]:
        price = self._base_price(query)
        return [{"id": f"mock2-{query.lower().replace(' ', '-')}", "name": query.title(), "price_inr": price, "in_stock": True}]

    def quote_cart(self, items: list[dict[str, Any]]) -> CartQuote:
        total = 0.0
        priced_items = []
        for item in items:
            name = str(item.get("ingredient", item.get("name", "item")))
            qty = float(item.get("quantity", item.get("missing_quantity", 1)))
            unit_price = self._base_price(name)
            line_total = round(unit_price * max(qty / 100.0, 1.0), 2)
            total += line_total
            priced_items.append({"name": name, "quantity": qty, "unit_price_inr": unit_price, "line_total_inr": line_total})
        return CartQuote(provider_name=self.provider_name, total_inr=round(total, 2), items=priced_items, delivery_charges_inr=15.0)

    def place_order(self, household_id: int, items: list[dict[str, Any]]) -> dict[str, Any]:
        quote = self.quote_cart(items)
        return {"provider": self.provider_name, "total_inr": quote.total_inr, "status": "mock_placed"}
