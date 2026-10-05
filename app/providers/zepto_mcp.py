"""Minimal Streamable-HTTP MCP adapter for Zepto — Ticket #20 refactor.

The live server's credentials are deployment-specific and are supplied through
local environment settings, which keeps tokens out of the browser and makes
mocked tests deterministic.

Ticket #20 change: this satisfies app.core.interfaces.CommerceProvider
(search / quote_cart / place_order) so app.services.consolidate_orders can
compare it against a second provider (commerce_mock.py) without special-
casing Zepto. The mock/live split below is the template the other rail
providers (payments, logistics) copy.

The token-acquisition half (PKCE authorize/callback) is deliberately absent:
no route ever wired it up, so it was removed rather than left as untested
scaffolding. A caller supplies an already-encrypted token.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
import httpx

from app.settings import Settings


class ZeptoMCPError(RuntimeError):
    pass


@dataclass
class CartQuote:
    provider_name: str
    total_inr: float
    items: list[dict[str, Any]] = field(default_factory=list)
    delivery_charges_inr: float = 0.0


class ZeptoMCPProvider:
    provider_name = "Zepto"

    def __init__(self, settings: Settings):
        self.settings = settings

    def _cipher(self) -> Fernet:
        if not self.settings.zepto_token_encryption_key:
            raise ZeptoMCPError("Zepto is not configured. Add OAuth settings and HOUSEHOLD_ZEPTO_TOKEN_ENCRYPTION_KEY to .env.")
        try:
            return Fernet(self.settings.zepto_token_encryption_key.encode())
        except (TypeError, ValueError) as exc:
            raise ZeptoMCPError("Zepto token encryption key is invalid.") from exc

    def decrypt_token(self, token: str) -> str:
        """Resolve a stored token for use against the live MCP server.

        With the mock enabled there is no live server and no encryption key
        to configure, so the supplied placeholder is passed straight through.
        Decrypting first would raise before the mock branch in `_tool_call`
        was ever reached, which silently disabled the whole Zepto rail.
        """
        if self.settings.zepto_mock_enabled:
            return token
        try:
            return self._cipher().decrypt(token.encode()).decode()
        except InvalidToken as exc:
            raise ZeptoMCPError("The stored Zepto connection is invalid. Reconnect your account.") from exc

    _MOCK_UNIT_PRICE_INR = 30.0

    def _mock_product(self, query: str) -> dict[str, Any]:
        return {
            "id": f"mock-{query.casefold().replace(' ', '-')}",
            "name": f"Fresh {query.title()}",
            "price_inr": self._MOCK_UNIT_PRICE_INR,
            "pack_size": "1 pack",
            "image": None,
            "in_stock": True,
        }

    def _mock_cart(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        """Price the requested items rather than echoing them back bare.

        The caller sends {ingredient, quantity, unit} with no price, so
        echoing the list unchanged made quote_cart sum to zero. A free
        basket is not a harmless mock: it wins the cheapest-provider
        comparison in consolidate_orders and then classifies as GREEN in
        classify_order_tier, which is the one tier that spends without
        asking a human. A mock rail must never look cheaper than a real one.

        Prices per pack, with a one-pack floor, matching how
        commerce_mock.CommerceMockProvider prices the competing basket --
        otherwise the cross-provider comparison is between two different
        units and whichever provider happens to be quoted "wins" for a
        reason that has nothing to do with price.
        """
        priced = []
        for item in items:
            quantity = float(item.get("quantity", item.get("missing_quantity", 1)) or 0)
            packs = max(quantity / 100.0, 1.0)
            line_total = round(self._MOCK_UNIT_PRICE_INR * packs, 2)
            priced.append(
                {
                    **item,
                    "name": str(item.get("ingredient", item.get("name", "item"))),
                    "quantity": quantity,
                    # quote_cart multiplies unit_price_inr by quantity, so the
                    # per-unit rate is derived from the pack-based line total.
                    "unit_price_inr": round(line_total / quantity, 6) if quantity else line_total,
                    "line_total_inr": line_total,
                }
            )
        return {"items": priced, "delivery_charges": 0}

    def _tool_call(self, token: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if self.settings.zepto_mock_enabled:
            if name == "search_products": return {"products": [self._mock_product(str(arguments.get("query", "item")))]}
            if name == "cart_management": return {"cart": self._mock_cart(arguments.get("items", []))}
            if name == "place_order": return {"payment_url": "upi://pay?pa=mock@zepto", "checkout_url": "https://www.zeptonow.com/cart"}
            return {"items": [], "total_amount_inr": 0, "delivery_charges": 0}
        request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}}
        try:
            response = httpx.post(self.settings.zepto_mcp_url, json=request, headers={"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream"}, timeout=self.settings.request_timeout_seconds)
            response.raise_for_status()
            text = response.text
            if text.startswith("data:"):
                text = next((line.removeprefix("data:").strip() for line in text.splitlines() if line.startswith("data:")), "{}")
            payload = json.loads(text)
            if payload.get("error"):
                raise ZeptoMCPError(str(payload["error"]))
            result = payload.get("result", {})
            if isinstance(result.get("structuredContent"), dict): return result["structuredContent"]
            for block in result.get("content", []):
                if isinstance(block, dict) and block.get("type") == "text":
                    try: return json.loads(block.get("text", "{}"))
                    except json.JSONDecodeError: continue
            return result if isinstance(result, dict) else {}
        except (httpx.HTTPError, ValueError) as exc:
            raise ZeptoMCPError("Zepto MCP request failed. Reconnect your account or try again.") from exc

    # -- CommerceProvider interface (Ticket #20) ----------------------------

    def search(self, encrypted_token: str, query: str) -> list[dict[str, Any]]:
        result = self._tool_call(self.decrypt_token(encrypted_token), "search_products", {"query": query})
        products = result.get("products", result.get("items", []))
        return [item for item in products if isinstance(item, dict)] if isinstance(products, list) else []

    def quote_cart(self, encrypted_token: str, items: list[dict[str, Any]]) -> CartQuote:
        result = self._tool_call(self.decrypt_token(encrypted_token), "cart_management", {"action": "add", "items": items})
        cart = result.get("cart", result)
        cart_items = cart.get("items", items)
        total = sum(float(i.get("unit_price_inr", i.get("price_inr", 0))) * float(i.get("quantity", 1)) for i in cart_items)
        return CartQuote(
            provider_name=self.provider_name,
            total_inr=round(total, 2),
            items=cart_items,
            delivery_charges_inr=float(cart.get("delivery_charges", 0)),
        )

    def place_order(self, encrypted_token: str, household_id: int, items: list[dict[str, Any]]) -> dict[str, Any]:
        """NOTE: this returns a UPI deep link / checkout URL — a human still
        has to tap to pay. Ticket #18's PaymentProvider (mock) and #40 (live
        Pine Labs) are what actually deliver tiered autonomy; this does not."""
        token = self.decrypt_token(encrypted_token)
        self._tool_call(token, "cart_management", {"action": "add", "items": items})
        return self._tool_call(token, "place_order", {"payment_method": "upi"})
