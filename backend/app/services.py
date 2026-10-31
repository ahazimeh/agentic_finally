"""Application services shared by REST routes and the chat agent.

Ties together the database, the price cache and the market data source so that
manual actions (REST) and AI actions (chat) go through identical code paths.
"""

from __future__ import annotations

import asyncio
import logging

from .database import DEFAULT_USER_ID, Database, new_id, now_iso
from .market import MarketDataSource, PriceCache
from .portfolio import (
    TradeResult,
    execute_trade,
    get_position_tickers,
    normalize_ticker,
    record_snapshot,
)

logger = logging.getLogger(__name__)

SNAPSHOT_INTERVAL_SECONDS = 30.0


class WatchlistError(ValueError):
    """Raised when a watchlist change is invalid."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


class TradingService:
    def __init__(
        self,
        db: Database,
        cache: PriceCache,
        source: MarketDataSource | None,
        user_id: str = DEFAULT_USER_ID,
    ) -> None:
        self.db = db
        self.cache = cache
        self.source = source
        self.user_id = user_id

    # ---- Watchlist -------------------------------------------------------

    def watchlist_tickers(self) -> list[str]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT ticker FROM watchlist WHERE user_id = ? ORDER BY rowid",
                (self.user_id,),
            ).fetchall()
        return [r["ticker"] for r in rows]

    def tracked_tickers(self) -> list[str]:
        """Union of watchlist and held positions — everything that needs live prices."""
        tickers = self.watchlist_tickers()
        for t in get_position_tickers(self.db, self.user_id):
            if t not in tickers:
                tickers.append(t)
        return tickers

    def get_watchlist(self) -> list[dict]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT ticker, added_at FROM watchlist WHERE user_id = ? "
                "ORDER BY rowid",
                (self.user_id,),
            ).fetchall()
        items = []
        for r in rows:
            update = self.cache.get(r["ticker"])
            items.append(
                {
                    "ticker": r["ticker"],
                    "added_at": r["added_at"],
                    "price": update.price if update else None,
                    "previous_price": update.previous_price if update else None,
                    "change": update.change if update else None,
                    "change_percent": update.change_percent if update else None,
                    "direction": update.direction if update else None,
                    "timestamp": update.timestamp if update else None,
                }
            )
        return items

    async def add_to_watchlist(self, ticker: str) -> str:
        try:
            ticker = normalize_ticker(ticker)
        except ValueError as e:
            raise WatchlistError(str(e)) from e
        with self.db.connect() as conn:
            exists = conn.execute(
                "SELECT 1 FROM watchlist WHERE user_id = ? AND ticker = ?",
                (self.user_id, ticker),
            ).fetchone()
            if exists:
                raise WatchlistError(f"{ticker} is already on the watchlist", status_code=409)
            conn.execute(
                "INSERT INTO watchlist (id, user_id, ticker, added_at) VALUES (?, ?, ?, ?)",
                (new_id(), self.user_id, ticker, now_iso()),
            )
        if self.source is not None:
            await self.source.add_ticker(ticker)
        return ticker

    async def remove_from_watchlist(self, ticker: str) -> str:
        try:
            ticker = normalize_ticker(ticker)
        except ValueError as e:
            raise WatchlistError(str(e)) from e
        with self.db.connect() as conn:
            cur = conn.execute(
                "DELETE FROM watchlist WHERE user_id = ? AND ticker = ?",
                (self.user_id, ticker),
            )
            if cur.rowcount == 0:
                raise WatchlistError(f"{ticker} is not on the watchlist", status_code=404)
        # Keep streaming prices for tickers we still hold so they stay valued live.
        if self.source is not None and ticker not in get_position_tickers(self.db, self.user_id):
            await self.source.remove_ticker(ticker)
        return ticker

    # ---- Trading ---------------------------------------------------------

    def trade(self, ticker: str, side: str, quantity: float) -> TradeResult:
        """Execute a trade and immediately record a portfolio snapshot."""
        result = execute_trade(self.db, self.cache, ticker, side, quantity, self.user_id)
        self.snapshot()
        return result

    # ---- Snapshots -------------------------------------------------------

    def snapshot(self) -> float:
        return record_snapshot(self.db, self.cache, self.user_id)

    async def snapshot_loop(self, interval: float = SNAPSHOT_INTERVAL_SECONDS) -> None:
        """Background task: record a portfolio snapshot every `interval` seconds."""
        while True:
            await asyncio.sleep(interval)
            try:
                self.snapshot()
            except Exception:
                logger.exception("Portfolio snapshot failed")
