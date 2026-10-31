"""Trade execution and P&L calculations."""

import pytest

from app.portfolio import (
    TradeError,
    execute_trade,
    get_history,
    get_portfolio,
    normalize_ticker,
    record_snapshot,
)


def _trade_count(db):
    with db.connect() as conn:
        return conn.execute("SELECT COUNT(*) AS n FROM trades").fetchone()["n"]


class TestBuy:
    def test_buy_creates_position_and_debits_cash(self, db, cache):
        r = execute_trade(db, cache, "AAPL", "buy", 10)
        assert r.price == 190.0
        assert r.cash_balance == pytest.approx(10000 - 1900)
        p = get_portfolio(db, cache)
        assert p["cash_balance"] == pytest.approx(8100)
        assert p["positions"][0]["ticker"] == "AAPL"
        assert p["positions"][0]["quantity"] == 10
        assert p["positions"][0]["avg_cost"] == 190.0
        assert p["total_value"] == pytest.approx(10000)

    def test_buy_twice_averages_cost(self, db, cache):
        execute_trade(db, cache, "AAPL", "buy", 10)
        cache.update("AAPL", 210.0)
        execute_trade(db, cache, "AAPL", "buy", 10)
        pos = get_portfolio(db, cache)["positions"][0]
        assert pos["quantity"] == 20
        assert pos["avg_cost"] == pytest.approx(200.0)

    def test_fractional_shares(self, db, cache):
        execute_trade(db, cache, "NVDA", "buy", 0.5)
        p = get_portfolio(db, cache)
        assert p["positions"][0]["quantity"] == 0.5
        assert p["cash_balance"] == pytest.approx(9600)

    def test_insufficient_cash(self, db, cache):
        with pytest.raises(TradeError, match="Insufficient cash"):
            execute_trade(db, cache, "NVDA", "buy", 13)  # 10,400 > 10,000
        p = get_portfolio(db, cache)
        assert p["cash_balance"] == 10000
        assert p["positions"] == []
        assert _trade_count(db) == 0

    def test_can_spend_exactly_all_cash(self, db, cache):
        cache.update("XYZ", 100.0)
        execute_trade(db, cache, "XYZ", "buy", 100)
        assert get_portfolio(db, cache)["cash_balance"] == 0

    def test_lowercase_ticker_normalized(self, db, cache):
        r = execute_trade(db, cache, " aapl ", "BUY", 1)
        assert r.ticker == "AAPL"
        assert r.side == "buy"


class TestSell:
    def test_partial_sell(self, db, cache):
        execute_trade(db, cache, "AAPL", "buy", 10)
        cache.update("AAPL", 200.0)
        r = execute_trade(db, cache, "AAPL", "sell", 4)
        assert r.position_quantity == 6
        p = get_portfolio(db, cache)
        assert p["cash_balance"] == pytest.approx(8100 + 800)
        assert p["positions"][0]["avg_cost"] == 190.0  # avg cost unchanged on sell

    def test_sell_all_removes_position(self, db, cache):
        execute_trade(db, cache, "AAPL", "buy", 10)
        execute_trade(db, cache, "AAPL", "sell", 10)
        p = get_portfolio(db, cache)
        assert p["positions"] == []
        assert p["cash_balance"] == pytest.approx(10000)

    def test_sell_all_fractional_float_noise(self, db, cache):
        execute_trade(db, cache, "AAPL", "buy", 0.1)
        execute_trade(db, cache, "AAPL", "buy", 0.2)
        execute_trade(db, cache, "AAPL", "sell", 0.3)  # 0.1 + 0.2 != 0.3 in floats
        assert get_portfolio(db, cache)["positions"] == []

    def test_sell_more_than_owned(self, db, cache):
        execute_trade(db, cache, "AAPL", "buy", 5)
        with pytest.raises(TradeError, match="Insufficient shares"):
            execute_trade(db, cache, "AAPL", "sell", 6)
        assert get_portfolio(db, cache)["positions"][0]["quantity"] == 5

    def test_sell_without_position(self, db, cache):
        with pytest.raises(TradeError, match="Insufficient shares"):
            execute_trade(db, cache, "AAPL", "sell", 1)

    def test_sell_at_loss(self, db, cache):
        execute_trade(db, cache, "TSLA", "buy", 10)  # 2500
        cache.update("TSLA", 200.0)
        execute_trade(db, cache, "TSLA", "sell", 10)  # 2000
        p = get_portfolio(db, cache)
        assert p["cash_balance"] == pytest.approx(9500)
        assert p["total_value"] == pytest.approx(9500)


class TestValidation:
    @pytest.mark.parametrize("qty", [0, -1, float("nan"), float("inf"), "abc"])
    def test_bad_quantity(self, db, cache, qty):
        with pytest.raises(TradeError):
            execute_trade(db, cache, "AAPL", "buy", qty)

    def test_bad_side(self, db, cache):
        with pytest.raises(TradeError, match="side"):
            execute_trade(db, cache, "AAPL", "hold", 1)

    @pytest.mark.parametrize("ticker", ["", "1ABC", "TOOLONGTICKER", "AA PL", "$$"])
    def test_bad_ticker(self, db, cache, ticker):
        with pytest.raises(TradeError):
            execute_trade(db, cache, ticker, "buy", 1)

    def test_no_price(self, db, cache):
        with pytest.raises(TradeError, match="No price"):
            execute_trade(db, cache, "ZZZZ", "buy", 1)

    def test_normalize_ticker(self):
        assert normalize_ticker("brk.b") == "BRK.B"
        with pytest.raises(ValueError):
            normalize_ticker("bad ticker")


class TestPnL:
    def test_unrealized_pnl_and_weights(self, db, cache):
        execute_trade(db, cache, "AAPL", "buy", 10)  # cost 1900
        execute_trade(db, cache, "MSFT", "buy", 5)  # cost 2100
        cache.update("AAPL", 209.0)  # +10%  -> 2090
        cache.update("MSFT", 399.0)  # -5%   -> 1995
        p = get_portfolio(db, cache)
        aapl, msft = p["positions"]
        assert aapl["unrealized_pnl"] == pytest.approx(190)
        assert aapl["unrealized_pnl_percent"] == pytest.approx(10.0)
        assert msft["unrealized_pnl"] == pytest.approx(-105)
        assert msft["unrealized_pnl_percent"] == pytest.approx(-5.0)
        assert p["unrealized_pnl"] == pytest.approx(85)
        assert p["unrealized_pnl_percent"] == pytest.approx(2.125, abs=0.01)
        assert p["positions_value"] == pytest.approx(4085)
        assert p["total_value"] == pytest.approx(6000 + 4085)
        assert aapl["weight"] + msft["weight"] == pytest.approx(100, abs=0.02)
        assert aapl["weight"] == pytest.approx(51.16, abs=0.01)

    def test_missing_price_falls_back_to_avg_cost(self, db, cache):
        execute_trade(db, cache, "AAPL", "buy", 10)
        cache.remove("AAPL")
        pos = get_portfolio(db, cache)["positions"][0]
        assert pos["current_price"] == 190.0
        assert pos["unrealized_pnl"] == 0

    def test_empty_portfolio(self, db, cache):
        p = get_portfolio(db, cache)
        assert p == {
            "cash_balance": 10000.0,
            "positions_value": 0.0,
            "total_value": 10000.0,
            "unrealized_pnl": 0.0,
            "unrealized_pnl_percent": 0.0,
            "positions": [],
        }


class TestSnapshots:
    def test_record_and_history_order(self, db, cache):
        record_snapshot(db, cache)
        execute_trade(db, cache, "AAPL", "buy", 10)
        cache.update("AAPL", 290.0)
        record_snapshot(db, cache)
        hist = get_history(db)
        assert [h["total_value"] for h in hist] == [10000.0, 11000.0]
        assert all("recorded_at" in h for h in hist)

    def test_history_limit_keeps_most_recent(self, db, cache):
        for price in (100.0, 200.0, 300.0):
            cache.update("AAPL", price)
            execute_trade(db, cache, "AAPL", "buy", 1)
            record_snapshot(db, cache)
        hist = get_history(db, limit=2)
        assert len(hist) == 2
        assert hist[-1]["total_value"] == pytest.approx(10000 - 600 + 900)
