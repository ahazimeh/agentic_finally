"""Chat orchestration: build context, call the LLM, auto-execute actions, persist history."""

from __future__ import annotations

import asyncio
import json
import logging

from .database import new_id, now_iso
from .llm import SYSTEM_PROMPT, LLMResponse, call_llm, is_mock_mode, mock_llm_response
from .portfolio import TradeError, get_portfolio
from .services import TradingService, WatchlistError

logger = logging.getLogger(__name__)

HISTORY_LIMIT = 20


def build_context(service: TradingService) -> tuple[dict, str]:
    portfolio = get_portfolio(service.db, service.cache, service.user_id)
    watchlist = [
        {"ticker": w["ticker"], "price": w["price"], "change_percent": w["change_percent"]}
        for w in service.get_watchlist()
    ]
    context = {"portfolio": portfolio, "watchlist": watchlist}
    return portfolio, json.dumps(context, indent=1)


def load_history(service: TradingService, limit: int = HISTORY_LIMIT) -> list[dict]:
    """Most recent messages in chronological order (role, content, actions, created_at)."""
    with service.db.connect() as conn:
        rows = conn.execute(
            "SELECT id, role, content, actions, created_at FROM ("
            "  SELECT id, role, content, actions, created_at, rowid AS rid FROM chat_messages"
            "  WHERE user_id = ? ORDER BY rid DESC LIMIT ?"
            ") ORDER BY rid ASC",
            (service.user_id, limit),
        ).fetchall()
    return [
        {
            "id": r["id"],
            "role": r["role"],
            "content": r["content"],
            "actions": json.loads(r["actions"]) if r["actions"] else None,
            "created_at": r["created_at"],
        }
        for r in rows
    ]


def _save_message(service: TradingService, role: str, content: str, actions: dict | None) -> None:
    with service.db.connect() as conn:
        conn.execute(
            "INSERT INTO chat_messages (id, user_id, role, content, actions, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                new_id(),
                service.user_id,
                role,
                content,
                json.dumps(actions) if actions is not None else None,
                now_iso(),
            ),
        )


async def _get_llm_response(user_message: str, portfolio: dict, messages: list[dict]) -> LLMResponse:
    if is_mock_mode():
        return mock_llm_response(user_message, portfolio)
    try:
        return await asyncio.to_thread(call_llm, messages)
    except Exception:
        logger.exception("LLM call failed")
        return LLMResponse(
            message="Sorry, I couldn't reach the AI service right now. Please try again shortly."
        )


async def execute_actions(service: TradingService, llm: LLMResponse) -> dict:
    """Run watchlist changes first (so newly added tickers get prices), then trades."""
    watchlist_results = []
    for change in llm.watchlist_changes:
        entry = {"ticker": change.ticker.upper(), "action": change.action}
        try:
            if change.action == "add":
                entry["ticker"] = await service.add_to_watchlist(change.ticker)
            else:
                entry["ticker"] = await service.remove_from_watchlist(change.ticker)
            entry["status"] = "ok"
        except WatchlistError as e:
            entry.update(status="error", error=str(e))
        watchlist_results.append(entry)

    trade_results = []
    for t in llm.trades:
        entry = {"ticker": t.ticker.upper(), "side": t.side, "quantity": t.quantity}
        try:
            result = service.trade(t.ticker, t.side, t.quantity)
            entry.update(result.to_dict(), status="executed")
        except TradeError as e:
            entry.update(status="error", error=str(e))
        trade_results.append(entry)

    return {"trades": trade_results, "watchlist_changes": watchlist_results}


async def handle_chat(service: TradingService, user_message: str) -> dict:
    portfolio, context = build_context(service)
    history = load_history(service)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "system", "content": f"Current portfolio and watchlist (live):\n{context}"},
    ]
    for h in history:
        content = h["content"]
        if h["role"] == "assistant" and h["actions"]:
            content += f"\n[Executed actions: {json.dumps(h['actions'])}]"
        messages.append({"role": h["role"], "content": content})
    messages.append({"role": "user", "content": user_message})

    _save_message(service, "user", user_message, None)
    llm = await _get_llm_response(user_message, portfolio, messages)
    actions = await execute_actions(service, llm)

    message = llm.message
    errors = [a["error"] for a in actions["trades"] + actions["watchlist_changes"] if "error" in a]
    if errors:
        message += "\n\nSome actions could not be completed: " + "; ".join(errors)

    has_actions = bool(actions["trades"] or actions["watchlist_changes"])
    _save_message(service, "assistant", message, actions if has_actions else None)
    return {"message": message, **actions}
