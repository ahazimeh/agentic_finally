"""Structured-output parsing and the deterministic mock."""

from app.llm import LLMResponse, is_mock_mode, mock_llm_response, parse_llm_response


class TestParse:
    def test_full_schema(self):
        r = parse_llm_response(
            '{"message": "Done", "trades": [{"ticker": "AAPL", "side": "buy", "quantity": 10}],'
            ' "watchlist_changes": [{"ticker": "PYPL", "action": "add"}]}'
        )
        assert r.message == "Done"
        assert r.trades[0].ticker == "AAPL" and r.trades[0].quantity == 10
        assert r.watchlist_changes[0].action == "add"

    def test_message_only(self):
        r = parse_llm_response('{"message": "Hi"}')
        assert r == LLMResponse(message="Hi")

    def test_null_lists(self):
        r = parse_llm_response('{"message": "Hi", "trades": null, "watchlist_changes": null}')
        assert r.message == "Hi" and r.trades == [] and r.watchlist_changes == []

    def test_code_fence(self):
        r = parse_llm_response('```json\n{"message": "fenced", "trades": []}\n```')
        assert r.message == "fenced"

    def test_surrounding_prose(self):
        r = parse_llm_response('Sure! {"message": "inner"} hope that helps')
        assert r.message == "inner"

    def test_plain_text_becomes_message(self):
        r = parse_llm_response("just text, not json")
        assert r.message == "just text, not json"
        assert r.trades == []

    def test_empty(self):
        assert parse_llm_response("").message
        assert parse_llm_response(None).message

    def test_invalid_trades_dropped_valid_kept(self):
        r = parse_llm_response(
            '{"message": "x", "trades": [{"ticker": "AAPL", "side": "hold", "quantity": 1},'
            ' {"ticker": "MSFT", "side": "sell", "quantity": 2}],'
            ' "watchlist_changes": [{"ticker": "X"}, {"ticker": "Y", "action": "remove"}]}'
        )
        assert [t.ticker for t in r.trades] == ["MSFT"]
        assert [c.ticker for c in r.watchlist_changes] == ["Y"]

    def test_json_array_falls_back_to_text(self):
        assert parse_llm_response("[1, 2]").message == "[1, 2]"


class TestMock:
    def test_buy_and_sell(self):
        r = mock_llm_response("Buy 10 aapl and sell 2.5 shares of TSLA")
        assert [(t.side, t.quantity, t.ticker) for t in r.trades] == [
            ("buy", 10, "AAPL"),
            ("sell", 2.5, "TSLA"),
        ]

    def test_watchlist(self):
        r = mock_llm_response("add PYPL to my watchlist and remove NFLX")
        assert [(c.action, c.ticker) for c in r.watchlist_changes] == [
            ("add", "PYPL"),
            ("remove", "NFLX"),
        ]

    def test_stopwords_ignored(self):
        assert mock_llm_response("add it please").watchlist_changes == []

    def test_deterministic(self):
        assert mock_llm_response("buy 1 AAPL") == mock_llm_response("buy 1 AAPL")

    def test_default_summary(self):
        portfolio = {"total_value": 10000.0, "cash_balance": 10000.0, "positions": []}
        r = mock_llm_response("how am I doing?", portfolio)
        assert "$10,000.00" in r.message
        assert r.trades == [] and r.watchlist_changes == []

    def test_mock_mode_env(self, monkeypatch):
        monkeypatch.setenv("LLM_MOCK", "true")
        assert is_mock_mode()
        monkeypatch.setenv("LLM_MOCK", "false")
        assert not is_mock_mode()
