# FinAlly — AI Trading Workstation

An AI-powered trading terminal that streams live market data, runs a simulated $10k portfolio, and includes a chat assistant that can analyze positions and execute trades in natural language. Built entirely by coding agents as the capstone of an agentic AI coding course.

## Features

- Live prices over SSE with green/red flash and sparklines
- Market-order trading with instant fills
- Portfolio heatmap, P&L chart, and positions table
- AI chat that analyzes holdings and auto-executes trades and watchlist changes
- Dark, Bloomberg-style layout

## Stack

Next.js (static export) + Tailwind · FastAPI (Python/uv) · SQLite · LiteLLM → OpenRouter (Cerebras) · GBM simulator or Massive API for market data. Everything runs in a single Docker container on port 8000.

## Quick Start

```bash
cp .env.example .env   # add OPENROUTER_API_KEY
docker build -t finally .
docker run -v finally-data:/app/db -p 8000:8000 --env-file .env finally
# open http://localhost:8000
```

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `OPENROUTER_API_KEY` | For chat | OpenRouter API key |
| `MASSIVE_API_KEY` | No | Real market data; simulator used if empty |
| `LLM_MOCK` | No | `true` for deterministic mock LLM responses |

## Project Structure

```
frontend/   Next.js app
backend/    FastAPI uv project
planning/   Spec (PLAN.md) and agent docs
test/       Playwright E2E tests
scripts/    Start/stop helpers
db/         SQLite volume mount
```

## License

See [LICENSE](LICENSE).
