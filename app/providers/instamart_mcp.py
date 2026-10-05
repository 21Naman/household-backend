"""Swiggy Instamart over MCP -- cart building only.

Covers the discover -> cart half of Instamart's flow: resolve an address,
search the catalogue, replace the cart, read it back. Checkout, payment and
order tracking are deliberately not implemented here, and the registry's
INSTAMART gate refuses any method outside the cart-building allowlist, so a
checkout cannot be reached even by name.

The mock is the default, like every other rail: the live server needs an
authenticated Swiggy session, and the app must boot and test offline.
"""
from __future__ import annotations

import json
import re
from typing import Any

import httpx

from app.settings import Settings


class InstamartMCPError(RuntimeError):
    pass


class InstamartMCPProvider:
    provider_name = "Instamart"

    _MOCK_PACK_PRICE_INR = 45.0
    _MOCK_DELIVERY_FEE_INR = 25.0
    # The mock otherwise fabricates a product for any query text, so there is
    # no input that reaches a genuine "no results" response. This sentinel
    # exists purely so that path is reachable from curl/manual testing, not
    # only from a monkeypatched unit test.
    NO_MATCH_SENTINEL = "no-match-test-item"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.live = not settings.instamart_mock_enabled and bool(settings.instamart_access_token)
        # The mock's stand-in for the one server-side cart a Swiggy account has.
        self._mock_cart_state: dict[str, Any] = {"items": [], "cartAbsent": True}

    # -- the five allowlisted tools -------------------------------------------

    def get_addresses(self) -> dict[str, Any]:
        return self._tool_call("get_addresses", {})

    def search_products(self, address_id: str, query: str, offset: int = 0) -> dict[str, Any]:
        return self._tool_call("search_products", {"addressId": address_id, "query": query, "offset": offset})

    def update_cart(self, address_id: str, items: list[dict[str, Any]]) -> dict[str, Any]:
        """Replaces the account's whole Instamart cart with `items`."""
        return self._tool_call("update_cart", {"selectedAddressId": address_id, "items": items})

    def get_cart(self) -> dict[str, Any]:
        return self._tool_call("get_cart", {})

    def clear_cart(self) -> dict[str, Any]:
        return self._tool_call("clear_cart", {})

    # -- transport --------------------------------------------------------------

    def _tool_call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if not self.live:
            return self._mock_tool_call(name, arguments)
        request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}}
        try:
            response = httpx.post(
                self.settings.instamart_mcp_url,
                json=request,
                headers={
                    "Authorization": f"Bearer {self.settings.instamart_access_token}",
                    "Accept": "application/json, text/event-stream",
                },
                timeout=self.settings.request_timeout_seconds,
            )
            response.raise_for_status()
            text = response.text
            if text.startswith("data:"):
                text = next(
                    (line.removeprefix("data:").strip() for line in text.splitlines() if line.startswith("data:")),
                    "{}",
                )
            payload = json.loads(text)
        except (httpx.HTTPError, ValueError) as exc:
            raise InstamartMCPError(f"Instamart MCP {name} request failed.") from exc
        if payload.get("error"):
            raise InstamartMCPError(f"Instamart MCP {name} returned an error: {payload['error']}")
        result = payload.get("result", {})
        if result.get("isError"):
            raise InstamartMCPError(f"Instamart MCP {name} returned an error: {_text_content(result)}")
        if isinstance(result.get("structuredContent"), dict):
            return result["structuredContent"]
        for block in result.get("content", []):
            if isinstance(block, dict) and block.get("type") == "text":
                try:
                    parsed = json.loads(block.get("text", "{}"))
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict):
                    return parsed
        return result if isinstance(result, dict) else {}

    # -- mock ---------------------------------------------------------------------

    def _mock_tool_call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "get_addresses":
            return {"data": [{"id": "mock-addr-home", "label": "Home", "displayAddress": "Mock Home, Bengaluru"}]}
        if name == "search_products":
            query = str(arguments.get("query", "item"))
            if query.strip().casefold() == self.NO_MATCH_SENTINEL:
                return {"data": {"products": []}}
            return {"data": {"products": [self._mock_product(query)]}}
        if name == "update_cart":
            self._mock_cart_state = self._mock_cart(arguments)
            return self._mock_cart_state
        if name == "get_cart":
            return self._mock_cart_state
        if name == "clear_cart":
            self._mock_cart_state = {"items": [], "cartAbsent": True}
            return self._mock_cart_state
        raise InstamartMCPError(f"Instamart mock has no tool named {name!r}")

    def _mock_product(self, query: str) -> dict[str, Any]:
        slug = re.sub(r"[^a-z0-9]+", "-", query.casefold()).strip("-") or "item"
        return {
            "productId": f"mock-product-{slug}",
            "parentProductId": f"mock-parent-{slug}",
            "displayName": f"Fresh {query.title()}",
            "inStock": True,
            "variations": [
                {
                    "spinId": f"mock-spin-{slug}",
                    "skuId": f"mock-sku-{slug}",
                    "displayName": f"Fresh {query.title()}",
                    "quantityDescription": "1 pack",
                    "price": {"offerPrice": self._MOCK_PACK_PRICE_INR},
                    "isInStockAndAvailable": True,
                }
            ],
        }

    def _mock_cart(self, arguments: dict[str, Any]) -> dict[str, Any]:
        items = [
            {
                "spinId": item["spinId"],
                "skuId": item.get("skuId"),
                "quantity": item["quantity"],
                "price": self._MOCK_PACK_PRICE_INR,
                "total": round(self._MOCK_PACK_PRICE_INR * item["quantity"], 2),
            }
            for item in arguments.get("items", [])
        ]
        item_total = round(sum(item["total"] for item in items), 2)
        return {
            "addressId": arguments.get("selectedAddressId"),
            "items": items,
            "bill": {
                "itemTotal": item_total,
                "deliveryFee": self._MOCK_DELIVERY_FEE_INR,
                "toPay": round(item_total + self._MOCK_DELIVERY_FEE_INR, 2),
            },
            "cartAbsent": not items,
        }


def _text_content(result: dict[str, Any]) -> str:
    texts = [block.get("text", "") for block in result.get("content", []) if isinstance(block, dict)]
    return " ".join(text for text in texts if text)[:300] or "no detail"
