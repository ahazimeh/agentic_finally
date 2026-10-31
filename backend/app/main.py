"""FastAPI application: REST API, SSE stream, background tasks, static frontend."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from dotenv import load_dotenv
from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .chat import handle_chat, load_history
from .database import BACKEND_DIR, REPO_ROOT, Database
from .market import MarketDataSource, PriceCache, create_market_data_source, create_stream_router
from .portfolio import TradeError, get_history, get_portfolio
from .services import SNAPSHOT_INTERVAL_SECONDS, TradingService, WatchlistError

logger = logging.getLogger(__name__)

load_dotenv(REPO_ROOT / ".env", override=False)


# ---- Request models -------------------------------------------------------


class TradeRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=10)
    quantity: float = Field(gt=0)
    side: str


class WatchlistAddRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=10)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


# ---- Routes ---------------------------------------------------------------


def _service(request: Request) -> TradingService:
    return request.app.state.service


def create_api_router() -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/health")
    async def health(request: Request) -> dict:
        svc = _service(request)
        return {"status": "ok", "tracked_tickers": len(svc.cache)}

    @router.get("/portfolio")
    async def portfolio(request: Request) -> dict:
        svc = _service(request)
        return get_portfolio(svc.db, svc.cache, svc.user_id)

    @router.post("/portfolio/trade")
    async def trade(body: TradeRequest, request: Request) -> dict:
        svc = _service(request)
        try:
            result = svc.trade(body.ticker, body.side, body.quantity)
        except TradeError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return {
            "trade": result.to_dict(),
            "portfolio": get_portfolio(svc.db, svc.cache, svc.user_id),
        }

    @router.get("/portfolio/history")
    async def history(request: Request) -> dict:
        svc = _service(request)
        return {"snapshots": get_history(svc.db, svc.user_id)}

    @router.get("/watchlist")
    async def watchlist(request: Request) -> dict:
        return {"tickers": _service(request).get_watchlist()}

    @router.post("/watchlist", status_code=201)
    async def add_watchlist(body: WatchlistAddRequest, request: Request) -> dict:
        svc = _service(request)
        try:
            ticker = await svc.add_to_watchlist(body.ticker)
        except WatchlistError as e:
            raise HTTPException(status_code=e.status_code, detail=str(e)) from e
        return {"ticker": ticker, "tickers": svc.get_watchlist()}

    @router.delete("/watchlist/{ticker}")
    async def remove_watchlist(ticker: str, request: Request) -> dict:
        svc = _service(request)
        try:
            removed = await svc.remove_from_watchlist(ticker)
        except WatchlistError as e:
            raise HTTPException(status_code=e.status_code, detail=str(e)) from e
        return {"ticker": removed, "tickers": svc.get_watchlist()}

    @router.post("/chat")
    async def chat(body: ChatRequest, request: Request) -> dict:
        return await handle_chat(_service(request), body.message)

    @router.get("/chat/history")
    async def chat_history(request: Request) -> dict:
        return {"messages": load_history(_service(request), limit=200)}

    return router


# ---- App factory ----------------------------------------------------------


def _static_dir() -> Path | None:
    env = os.environ.get("FINALLY_STATIC_DIR", "").strip()
    path = Path(env) if env else BACKEND_DIR / "static"
    return path if path.is_dir() else None


def create_app(
    db_path: str | Path | None = None,
    market_source: MarketDataSource | None = None,
    price_cache: PriceCache | None = None,
    snapshot_interval: float = SNAPSHOT_INTERVAL_SECONDS,
    enable_background_tasks: bool = True,
) -> FastAPI:
    """Build the FastAPI app.

    Tests can inject a price cache / market source and disable background tasks.
    """
    cache = price_cache if price_cache is not None else PriceCache()
    db = Database(db_path)
    source = market_source
    if source is None and enable_background_tasks:
        source = create_market_data_source(cache)
    service = TradingService(db, cache, source)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db.initialize()
        tasks: list[asyncio.Task] = []
        if enable_background_tasks and source is not None:
            await source.start(service.tracked_tickers())
            service.snapshot()
            tasks.append(
                asyncio.create_task(service.snapshot_loop(snapshot_interval), name="snapshots")
            )
        try:
            yield
        finally:
            for t in tasks:
                t.cancel()
                with suppress(asyncio.CancelledError):
                    await t
            if enable_background_tasks and source is not None:
                await source.stop()

    app = FastAPI(title="FinAlly", version="0.1.0", lifespan=lifespan)
    app.state.service = service
    app.include_router(create_api_router())
    app.include_router(create_stream_router(cache))

    static = _static_dir()
    if static is not None:
        app.mount("/", StaticFiles(directory=static, html=True), name="static")

    return app


app = create_app()
