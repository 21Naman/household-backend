"""Instamart cart building: search -> update_cart -> get_cart, never checkout."""
from __future__ import annotations

import json as json_module

import httpx
import pytest

from app.core.container import get_container
from app.core.registry import ToolCallRefused, ToolKind, ToolRegistry
from app.providers.instamart_mcp import InstamartMCPError, InstamartMCPProvider
from app.settings import Settings


def _loop(client, name="Instamart HH"):
    hid = client.post("/api/households", json={"name": name}).json()["id"]
    loop_id = client.post(f"/api/households/{hid}/loops", json={"trigger_type": "manual"}).json()["id"]
    return hid, loop_id


def _build(client, hid, loop_id, ingredients, address="mock-addr-home"):
    return client.post(
        f"/api/households/{hid}/loops/{loop_id}/instamart-cart",
        json={"selectedAddressId": address, "missingIngredients": ingredients},
    )


# -- the gate ------------------------------------------------------------------

@pytest.mark.parametrize("method", ["checkout", "confirm_order", "get_payment_options", "track_order"])
def test_gate_refuses_every_order_and_payment_method(method):
    registry = ToolRegistry()
    registry.register(ToolKind.INSTAMART, InstamartMCPProvider(Settings()))

    with pytest.raises(ToolCallRefused, match="only cart building"):
        registry.invoke(ToolKind.INSTAMART, method, {"household_id": 1})


def test_gate_requires_a_household():
    registry = ToolRegistry()
    registry.register(ToolKind.INSTAMART, InstamartMCPProvider(Settings()))

    with pytest.raises(ToolCallRefused, match="household_id"):
        registry.invoke(ToolKind.INSTAMART, "get_cart", {})


# -- endpoints -------------------------------------------------------------------

def test_addresses_are_listed_for_the_picker(api_client):
    client, _ = api_client
    hid, _loop_id = _loop(client)

    body = client.get(f"/api/households/{hid}/instamart/addresses").json()

    assert body["data"][0]["id"] == "mock-addr-home"


def test_builds_cart_from_missing_ingredients_and_persists_a_snapshot(api_client):
    client, _ = api_client
    hid, loop_id = _loop(client)

    response = _build(
        client,
        hid,
        loop_id,
        [
            {"ingredient": "Eggs", "quantity": 2.5, "unit": "count"},
            {"ingredient": "Basmati Rice", "quantity": 500, "unit": "g"},
        ],
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert [m["cart_quantity"] for m in body["matched_items"]] == [3, 1]
    assert body["unmatched_items"] == []
    assert body["live_cart"]["addressId"] == "mock-addr-home"
    assert {i["spinId"]: i["quantity"] for i in body["live_cart"]["items"]} == {
        "mock-spin-eggs": 3,
        "mock-spin-basmati-rice": 1,
    }
    assert body["live_cart"]["bill"]["toPay"] > 0

    stored = client.get(f"/api/households/{hid}/loops/{loop_id}/instamart-cart").json()
    assert stored["id"] == body["snapshot_id"]
    assert stored["live_cart_json"] == body["live_cart"]
    events = [e["event"] for e in client.get(f"/api/households/{hid}/audit").json()]
    assert "instamart_cart_built" in events


def test_out_of_stock_variants_are_reported_not_carted(api_client, monkeypatch):
    client, _ = api_client
    hid, loop_id = _loop(client)
    instamart = get_container().instamart
    monkeypatch.setattr(
        instamart,
        "search_products",
        lambda address_id, query: {
            "data": {"products": [{"inStock": False, "variations": [{"spinId": "s1", "isInStockAndAvailable": False}]}]}
        },
    )
    update_calls = []
    monkeypatch.setattr(instamart, "update_cart", lambda *a: update_calls.append(a))

    body = _build(client, hid, loop_id, [{"ingredient": "Saffron", "quantity": 1, "unit": "g"}]).json()

    assert body["matched_items"] == []
    assert body["unmatched_items"][0]["reason"] == "out_of_stock"
    assert update_calls == [], "a cart with nothing matched must leave the account's cart alone"
    assert body["live_cart"] == {}


def test_a_failed_search_skips_only_that_ingredient(api_client, monkeypatch):
    client, _ = api_client
    hid, loop_id = _loop(client)
    instamart = get_container().instamart
    real_search = instamart.search_products

    def flaky(address_id, query):
        if query == "Paneer":
            raise InstamartMCPError("boom")
        return real_search(address_id, query)

    monkeypatch.setattr(instamart, "search_products", flaky)

    body = _build(
        client,
        hid,
        loop_id,
        [{"ingredient": "Paneer", "quantity": 200, "unit": "g"}, {"ingredient": "Onion", "quantity": 2, "unit": "pcs"}],
    ).json()

    assert [u["reason"] for u in body["unmatched_items"]] == ["search_failed"]
    assert [m["ingredient"] for m in body["matched_items"]] == ["Onion"]


def test_update_cart_failure_is_a_502_with_an_audit_row(api_client, monkeypatch):
    client, _ = api_client
    hid, loop_id = _loop(client)

    def fail(*_args):
        raise InstamartMCPError("Instamart MCP update_cart request failed.")

    monkeypatch.setattr(get_container().instamart, "update_cart", fail)

    response = _build(client, hid, loop_id, [{"ingredient": "Milk", "quantity": 1, "unit": "l"}])

    assert response.status_code == 502
    events = [e["event"] for e in client.get(f"/api/households/{hid}/audit").json()]
    assert "instamart_cart_failed" in events


def test_another_households_loop_is_not_found(api_client):
    client, _ = api_client
    mine, _ = _loop(client, "Mine")
    _theirs, their_loop = _loop(client, "Theirs")

    response = _build(client, mine, their_loop, [{"ingredient": "Milk", "quantity": 1, "unit": "l"}])

    assert response.status_code == 404


def test_snapshot_read_is_404_before_any_cart_is_built(api_client):
    client, _ = api_client
    hid, loop_id = _loop(client)

    assert client.get(f"/api/households/{hid}/loops/{loop_id}/instamart-cart").status_code == 404


# -- live transport ----------------------------------------------------------------

def _live_provider():
    return InstamartMCPProvider(Settings(instamart_mock_enabled=False, instamart_access_token="tok"))


def test_live_rail_needs_a_token_and_the_mock_flag_off():
    assert not InstamartMCPProvider(Settings(instamart_mock_enabled=False)).live
    assert not InstamartMCPProvider(Settings(instamart_access_token="tok")).live
    assert _live_provider().live


def test_live_update_cart_sends_the_mcp_tool_call(monkeypatch):
    sent = {}

    def fake_post(url, json, headers, timeout):
        sent.update(url=url, body=json, headers=headers)
        payload = {"jsonrpc": "2.0", "id": 1, "result": {"structuredContent": {"items": [{"spinId": "s1"}]}}}
        return httpx.Response(200, text="data: " + json_module.dumps(payload), request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)

    result = _live_provider().update_cart("addr-1", [{"spinId": "s1", "skuId": "k1", "quantity": 2}])

    assert result == {"items": [{"spinId": "s1"}]}
    assert sent["headers"]["Authorization"] == "Bearer tok"
    assert sent["body"]["method"] == "tools/call"
    assert sent["body"]["params"] == {
        "name": "update_cart",
        "arguments": {"selectedAddressId": "addr-1", "items": [{"spinId": "s1", "skuId": "k1", "quantity": 2}]},
    }


def test_live_tool_error_result_raises(monkeypatch):
    def fake_post(url, json, headers, timeout):
        payload = {"result": {"isError": True, "content": [{"type": "text", "text": "Address not serviceable"}]}}
        return httpx.Response(200, text=json_module.dumps(payload), request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)

    with pytest.raises(InstamartMCPError, match="Address not serviceable"):
        _live_provider().search_products("addr-1", "milk")
