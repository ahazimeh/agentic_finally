# Backend — Developer Guide

## Project Setup

```bash
cd backend
uv sync --extra dev   # Install all dependencies including test/lint tools
```

## Market Data API

The market data subsystem lives in `app/market/`. Use these imports:

```python
from app.market import PriceCache, PriceUpdate, MarketDataSource, create_market_data_source
```

### Core Types

- **`PriceUpdate`** — Immutable dataclass: `ticker`, `price`, `previous_price`, `timestamp`, plus properties `change`, `change_percent`, `direction` ("up"/"down"/"flat"), and `to_dict()` for JSON serialization.

- **`PriceCache`** — Thread-safe in-memory store. Key methods:
  - `update(ticker, price, timestamp=None) -> PriceUpdate`
  - `get(ticker) -> PriceUpdate | None`
  - `get_price(ticker) -> float | None`
  - `get_all() -> dict[str, PriceUpdate]`
  - `remove(ticker)`
  - `version` property — monotonic counter, increments on every update (for SSE change detection)

- **`MarketDataSource`** — Abstract interface implemented by `SimulatorDataSource` and `MassiveDataSource`. Lifecycle: `start(tickers)` -> `add_ticker()` / `remove_ticker()` -> `stop()`.

- **`create_market_data_source(cache)`** — Factory. Returns `MassiveDataSource` if `MASSIVE_API_KEY` is set, otherwise `SimulatorDataSource`.

### SSE Streaming

```python
from app.market import create_stream_router

router = create_stream_router(price_cache)  # Returns FastAPI APIRouter
# Endpoint: GET /api/stream/prices (text/event-stream)
```

### Seed Data

Default tickers: AAPL, GOOGL, MSFT, AMZN, TSLA, NVDA, META, JPM, V, NFLX. Seed prices and per-ticker volatility/drift params are in `app/market/seed_prices.py`.

## Application (API, DB, Chat)

Entry point: `app.main:app` (`uv run uvicorn app.main:app --port 8000`). `create_app()` accepts
`db_path`, `market_source`, `price_cache`, `snapshot_interval`, `enable_background_tasks` for tests.

| Module | Purpose |
|--------|---------|
| `app/database.py` | `Database` — lazy init: runs `db/schema.sql` always, `db/seed.sql` only on a fresh DB |
| `app/portfolio.py` | `execute_trade`, `get_portfolio` (P&L, weights), `record_snapshot`, `get_history` |
| `app/services.py` | `TradingService` — watchlist add/remove (keeps market source in sync), trade + snapshot, 30s snapshot loop |
| `app/llm.py` | `LLMResponse` schema, `parse_llm_response`, `mock_llm_response` (LLM_MOCK), `call_llm` (LiteLLM → OpenRouter/Cerebras) |
| `app/chat.py` | `handle_chat` — context + history → LLM → auto-execute actions → persist |
| `app/main.py` | FastAPI app, routes, lifespan (starts market source + snapshot task), static files from `backend/static` |

Env vars: `FINALLY_DB_PATH` (default `<repo>/db/finally.db`), `FINALLY_STATIC_DIR` (default
`backend/static`), `LLM_MOCK`, `OPENROUTER_API_KEY`, `MASSIVE_API_KEY`. `.env` at repo root is loaded.

Endpoints: `GET /api/health`, `GET /api/portfolio`, `POST /api/portfolio/trade` `{ticker, quantity, side}`
(400 on validation failure), `GET /api/portfolio/history` → `{snapshots: [{total_value, recorded_at}]}`,
`GET /api/watchlist` → `{tickers: [...]}`, `POST /api/watchlist` `{ticker}` (201; 409 duplicate),
`DELETE /api/watchlist/{ticker}` (404 if absent), `POST /api/chat` `{message}` →
`{message, trades: [...status], watchlist_changes: [...status]}`, `GET /api/chat/history`,
`GET /api/stream/prices` (SSE).

Mock LLM understands: "buy 10 AAPL", "sell 5 shares of TSLA", "add PYPL", "remove NFLX".

Docker note: place the backend at `/app/backend` and mount the volume at `/app/db` so the
default DB path resolves and `backend/db/*.sql` is not shadowed by the volume.

## Running Tests

```bash
uv run --extra dev pytest -v              # All tests
uv run --extra dev pytest --cov=app       # With coverage
uv run --extra dev ruff check app/ tests/ # Lint
```

## Demo

```bash
uv run market_data_demo.py   # Live terminal dashboard with simulated prices
```
