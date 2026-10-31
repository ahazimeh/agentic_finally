"""Fixtures for database, portfolio, chat and API tests."""

import pytest
from fastapi.testclient import TestClient

from app.database import Database
from app.main import create_app
from app.market import MarketDataSource, PriceCache
from app.services import TradingService

PRICES = {
    "AAPL": 190.0, "GOOGL": 175.0, "MSFT": 420.0, "AMZN": 185.0, "TSLA": 250.0,
    "NVDA": 800.0, "META": 500.0, "JPM": 195.0, "V": 280.0, "NFLX": 600.0,
}


class FakeSource(MarketDataSource):
    """Deterministic market source: new tickers get a fixed price of 100."""

    def __init__(self, cache: PriceCache) -> None:
        self.cache = cache
        self.tickers: list[str] = []
        self.started = False

    async def start(self, tickers):
        self.started = True
        for t in tickers:
            await self.add_ticker(t)

    async def stop(self):
        self.started = False

    async def add_ticker(self, ticker):
        if ticker not in self.tickers:
            self.tickers.append(ticker)
        if self.cache.get_price(ticker) is None:
            self.cache.update(ticker, PRICES.get(ticker, 100.0))

    async def remove_ticker(self, ticker):
        if ticker in self.tickers:
            self.tickers.remove(ticker)
        self.cache.remove(ticker)

    def get_tickers(self):
        return list(self.tickers)


@pytest.fixture
def cache() -> PriceCache:
    c = PriceCache()
    for t, p in PRICES.items():
        c.update(t, p)
    return c


@pytest.fixture
def db(tmp_path) -> Database:
    return Database(tmp_path / "test.db")


@pytest.fixture
def source(cache) -> FakeSource:
    return FakeSource(cache)


@pytest.fixture
def service(db, cache, source) -> TradingService:
    return TradingService(db, cache, source)


@pytest.fixture(autouse=True)
def mock_llm(monkeypatch):
    monkeypatch.setenv("LLM_MOCK", "true")


@pytest.fixture
def client(tmp_path, cache, source):
    app = create_app(
        db_path=tmp_path / "api.db",
        market_source=source,
        price_cache=cache,
        snapshot_interval=3600,
    )
    with TestClient(app) as c:
        yield c
