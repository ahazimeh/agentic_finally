"""HTTP routes: status codes and response shapes."""

import asyncio
import json

import pytest

from app.market.stream import _generate_events


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_startup_starts_source_and_snapshots(client, source):
    assert source.started
    assert len(source.tickers) == 10
    assert len(client.get("/api/portfolio/history").json()["snapshots"]) == 1


def test_portfolio_initial(client):
    r = client.get("/api/portfolio")
    assert r.status_code == 200
    body = r.json()
    assert body["cash_balance"] == 10000.0
    assert body["total_value"] == 10000.0
    assert body["positions"] == []


class TestTrade:
    def test_buy(self, client):
        r = client.post("/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 5, "side": "buy"})
        assert r.status_code == 200
        body = r.json()
        assert body["trade"]["price"] == 190.0
        assert body["trade"]["total"] == 950.0
        assert body["portfolio"]["cash_balance"] == 9050.0
        assert body["portfolio"]["positions"][0]["ticker"] == "AAPL"

    def test_trade_records_snapshot(self, client):
        client.post("/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 1, "side": "buy"})
        assert len(client.get("/api/portfolio/history").json()["snapshots"]) == 2

    def test_insufficient_cash_400(self, client):
        r = client.post("/api/portfolio/trade", json={"ticker": "NVDA", "quantity": 100, "side": "buy"})
        assert r.status_code == 400
        assert "Insufficient cash" in r.json()["detail"]

    def test_sell_unowned_400(self, client):
        r = client.post("/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 1, "side": "sell"})
        assert r.status_code == 400

    def test_bad_side_400(self, client):
        r = client.post("/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 1, "side": "short"})
        assert r.status_code == 400

    @pytest.mark.parametrize(
        "body",
        [{"ticker": "AAPL", "side": "buy"}, {"ticker": "AAPL", "quantity": 0, "side": "buy"},
         {"ticker": "AAPL", "quantity": -3, "side": "buy"}, {"quantity": 1, "side": "buy"}],
    )
    def test_invalid_body_422(self, client, body):
        assert client.post("/api/portfolio/trade", json=body).status_code == 422


def test_history_shape(client):
    snaps = client.get("/api/portfolio/history").json()["snapshots"]
    assert set(snaps[0]) == {"total_value", "recorded_at"}


class TestWatchlist:
    def test_get(self, client):
        r = client.get("/api/watchlist")
        assert r.status_code == 200
        items = r.json()["tickers"]
        assert [i["ticker"] for i in items][:3] == ["AAPL", "GOOGL", "MSFT"]
        assert items[0]["price"] == 190.0
        assert {"previous_price", "change", "change_percent", "direction", "added_at"} <= set(items[0])

    def test_add_and_remove(self, client):
        r = client.post("/api/watchlist", json={"ticker": "pypl"})
        assert r.status_code == 201
        assert r.json()["ticker"] == "PYPL"
        assert "PYPL" in [i["ticker"] for i in client.get("/api/watchlist").json()["tickers"]]

        r = client.delete("/api/watchlist/PYPL")
        assert r.status_code == 200
        assert "PYPL" not in [i["ticker"] for i in r.json()["tickers"]]

    def test_add_duplicate_409(self, client):
        assert client.post("/api/watchlist", json={"ticker": "AAPL"}).status_code == 409

    def test_add_invalid_400(self, client):
        assert client.post("/api/watchlist", json={"ticker": "a b"}).status_code == 400

    def test_remove_missing_404(self, client):
        assert client.delete("/api/watchlist/ZZZZ").status_code == 404


class TestChat:
    def test_chat_mock_executes_trade(self, client):
        r = client.post("/api/chat", json={"message": "buy 3 AAPL"})
        assert r.status_code == 200
        body = r.json()
        assert body["message"]
        assert body["trades"][0]["status"] == "executed"
        assert client.get("/api/portfolio").json()["positions"][0]["quantity"] == 3

    def test_chat_watchlist(self, client):
        body = client.post("/api/chat", json={"message": "add PYPL"}).json()
        assert body["watchlist_changes"] == [{"ticker": "PYPL", "action": "add", "status": "ok"}]

    def test_chat_empty_message_422(self, client):
        assert client.post("/api/chat", json={"message": ""}).status_code == 422

    def test_chat_history(self, client):
        client.post("/api/chat", json={"message": "hello"})
        msgs = client.get("/api/chat/history").json()["messages"]
        assert [m["role"] for m in msgs] == ["user", "assistant"]


def test_sse_route_registered(client):
    paths = {r.path for r in client.app.routes}
    assert "/api/stream/prices" in paths


async def test_sse_generator_emits_prices(cache):
    class FakeRequest:
        client = None
        calls = 0

        async def is_disconnected(self):
            self.calls += 1
            return self.calls > 1

    gen = _generate_events(cache, FakeRequest(), interval=0.01)
    events = [e async for e in gen]
    assert events[0].startswith("retry:")
    payload = json.loads(events[1].removeprefix("data: ").strip())
    assert payload["AAPL"]["price"] == 190.0
    assert payload["AAPL"]["direction"] == "flat"
    await asyncio.sleep(0)
