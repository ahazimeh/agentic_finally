"""Watchlist service, snapshot loop and chat flow."""

import asyncio

import pytest

from app import chat as chat_module
from app.chat import handle_chat, load_history
from app.llm import LLMResponse, TradeInstruction
from app.portfolio import get_history, get_portfolio
from app.services import WatchlistError


class TestWatchlist:
    async def test_add_registers_with_source(self, service, source):
        assert await service.add_to_watchlist("pypl") == "PYPL"
        assert "PYPL" in source.tickers
        assert service.watchlist_tickers()[-1] == "PYPL"
        assert service.get_watchlist()[-1]["price"] == 100.0

    async def test_add_duplicate(self, service):
        with pytest.raises(WatchlistError) as e:
            await service.add_to_watchlist("AAPL")
        assert e.value.status_code == 409

    async def test_add_invalid(self, service):
        with pytest.raises(WatchlistError) as e:
            await service.add_to_watchlist("not valid")
        assert e.value.status_code == 400

    async def test_remove(self, service, source, cache):
        source.tickers.append("NFLX")
        await service.remove_from_watchlist("NFLX")
        assert "NFLX" not in service.watchlist_tickers()
        assert cache.get("NFLX") is None

    async def test_remove_missing(self, service):
        with pytest.raises(WatchlistError) as e:
            await service.remove_from_watchlist("ZZZ")
        assert e.value.status_code == 404

    async def test_remove_keeps_price_for_held_position(self, service, cache):
        service.trade("AAPL", "buy", 1)
        await service.remove_from_watchlist("AAPL")
        assert cache.get_price("AAPL") == 190.0
        assert "AAPL" in service.tracked_tickers()


class TestSnapshots:
    def test_trade_records_snapshot(self, service, db):
        service.trade("AAPL", "buy", 1)
        service.trade("AAPL", "sell", 1)
        assert len(get_history(db)) == 2

    async def test_snapshot_loop(self, service, db):
        task = asyncio.create_task(service.snapshot_loop(interval=0.01))
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(get_history(db)) >= 3


class TestChat:
    async def test_mock_trade_and_watchlist(self, service, db, cache):
        result = await handle_chat(service, "add PYPL and buy 2 PYPL and buy 1 AAPL")
        assert [w["status"] for w in result["watchlist_changes"]] == ["ok"]
        assert [(t["ticker"], t["status"]) for t in result["trades"]] == [
            ("PYPL", "executed"),
            ("AAPL", "executed"),
        ]
        p = get_portfolio(db, cache)
        assert p["cash_balance"] == pytest.approx(10000 - 200 - 190)

    async def test_failed_trade_reported(self, service):
        result = await handle_chat(service, "buy 1000 NVDA")
        assert result["trades"][0]["status"] == "error"
        assert "Insufficient cash" in result["trades"][0]["error"]
        assert "could not be completed" in result["message"]

    async def test_history_persisted(self, service):
        await handle_chat(service, "hello")
        await handle_chat(service, "buy 1 AAPL")
        history = load_history(service)
        assert [h["role"] for h in history] == ["user", "assistant", "user", "assistant"]
        assert history[1]["actions"] is None
        assert history[3]["actions"]["trades"][0]["ticker"] == "AAPL"

    async def test_real_llm_path_builds_prompt(self, service, monkeypatch):
        monkeypatch.setenv("LLM_MOCK", "false")
        captured = {}

        def fake_call(messages):
            captured["messages"] = messages
            return LLMResponse(
                message="ok", trades=[TradeInstruction(ticker="MSFT", side="buy", quantity=1)]
            )

        monkeypatch.setattr(chat_module, "call_llm", fake_call)
        result = await handle_chat(service, "buy me some msft")
        assert result["trades"][0]["status"] == "executed"
        msgs = captured["messages"]
        assert msgs[0]["role"] == "system" and "FinAlly" in msgs[0]["content"]
        assert '"cash_balance": 10000.0' in msgs[1]["content"]
        assert msgs[-1] == {"role": "user", "content": "buy me some msft"}

    async def test_llm_failure_is_graceful(self, service, monkeypatch):
        monkeypatch.setenv("LLM_MOCK", "false")

        def boom(messages):
            raise RuntimeError("network down")

        monkeypatch.setattr(chat_module, "call_llm", boom)
        result = await handle_chat(service, "hi")
        assert "couldn't reach" in result["message"]
        assert result["trades"] == []
