"""LLM integration: structured output schema, real (LiteLLM/OpenRouter/Cerebras) and mock clients."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger(__name__)

MODEL = "openrouter/openai/gpt-oss-120b"
EXTRA_BODY = {"provider": {"order": ["cerebras"]}}


class TradeInstruction(BaseModel):
    ticker: str
    side: Literal["buy", "sell"]
    quantity: float


class WatchlistChange(BaseModel):
    ticker: str
    action: Literal["add", "remove"]


class LLMResponse(BaseModel):
    message: str
    trades: list[TradeInstruction] = Field(default_factory=list)
    watchlist_changes: list[WatchlistChange] = Field(default_factory=list)


SYSTEM_PROMPT = """You are FinAlly, an AI trading assistant inside a simulated trading workstation.
The user trades a virtual portfolio with fake money; market orders fill instantly at the current price.

Your job:
- Analyze portfolio composition, risk concentration and P&L.
- Suggest trades with brief reasoning.
- Execute trades when the user asks or agrees, by listing them in "trades".
- Manage the watchlist proactively via "watchlist_changes" (action "add" or "remove").
- Be concise and data-driven. Use the numbers in the portfolio context.
- Only trade tickers that have a live price (on the watchlist or held). To trade a new ticker,
  add it to the watchlist first and tell the user to retry.
- Never invent trades the user did not ask for or agree to.

Always respond with valid JSON matching this schema:
{"message": "<text for the user>",
 "trades": [{"ticker": "AAPL", "side": "buy", "quantity": 10}],
 "watchlist_changes": [{"ticker": "PYPL", "action": "add"}]}
"trades" and "watchlist_changes" may be empty lists."""


def is_mock_mode() -> bool:
    return os.environ.get("LLM_MOCK", "").strip().lower() in ("1", "true", "yes", "on")


def parse_llm_response(raw: str | None) -> LLMResponse:
    """Parse the model's raw text into an LLMResponse, tolerating common malformations.

    Handles markdown code fences and leading/trailing prose around a JSON object.
    Invalid individual trades/watchlist entries are dropped rather than failing the
    whole response. If nothing usable is found, the raw text becomes the message.
    """
    text = (raw or "").strip()
    if not text:
        return LLMResponse(message="Sorry, I didn't get a response. Please try again.")

    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    candidate = fenced.group(1).strip() if fenced else text
    if not candidate.startswith("{"):
        start, end = candidate.find("{"), candidate.rfind("}")
        if start != -1 and end > start:
            candidate = candidate[start : end + 1]

    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        return LLMResponse(message=text)
    if not isinstance(data, dict):
        return LLMResponse(message=text)

    try:
        return LLMResponse.model_validate(data)
    except ValidationError:
        pass

    # Salvage: keep the message and any individually-valid actions.
    message = data.get("message")
    if not isinstance(message, str) or not message.strip():
        message = "Done." if (data.get("trades") or data.get("watchlist_changes")) else text
    trades = []
    for t in data.get("trades") or []:
        try:
            trades.append(TradeInstruction.model_validate(t))
        except ValidationError:
            logger.warning("Dropping malformed trade from LLM: %r", t)
    changes = []
    for c in data.get("watchlist_changes") or []:
        try:
            changes.append(WatchlistChange.model_validate(c))
        except ValidationError:
            logger.warning("Dropping malformed watchlist change from LLM: %r", c)
    return LLMResponse(message=message, trades=trades, watchlist_changes=changes)


# ---- Mock client --------------------------------------------------------

_TICKER = r"([A-Za-z][A-Za-z0-9.\-]{0,9})"
_TRADE_RE = re.compile(
    rf"\b(buy|sell)\s+(\d+(?:\.\d+)?)\s+(?:shares?\s+(?:of\s+)?)?\$?{_TICKER}\b", re.IGNORECASE
)
_ADD_RE = re.compile(rf"\b(?:add|watch)\s+\$?{_TICKER}\b", re.IGNORECASE)
_REMOVE_RE = re.compile(rf"\b(?:remove|unwatch|drop)\s+\$?{_TICKER}\b", re.IGNORECASE)
# Words that follow "add"/"remove" in plain English and are never meant as tickers.
_STOPWORDS = {"A", "AN", "THE", "IT", "TO", "MY", "SOME", "THAT", "THIS", "THEM", "FROM", "ME"}


def mock_llm_response(user_message: str, portfolio: dict | None = None) -> LLMResponse:
    """Deterministic response derived from simple patterns in the user's message.

    - "buy 10 AAPL" / "sell 5 shares of TSLA" → trades
    - "add PYPL" / "watch PYPL" → watchlist add
    - "remove NFLX" → watchlist remove
    - anything else → a canned portfolio summary
    """
    trades = [
        TradeInstruction(side=m.group(1).lower(), quantity=float(m.group(2)), ticker=m.group(3).upper())
        for m in _TRADE_RE.finditer(user_message)
    ]
    changes = [
        WatchlistChange(ticker=m.group(1).upper(), action=action)
        for action, regex in (("add", _ADD_RE), ("remove", _REMOVE_RE))
        for m in regex.finditer(user_message)
        if m.group(1).upper() not in _STOPWORDS
    ]

    parts = []
    if trades:
        parts.append(
            "Executing: " + ", ".join(f"{t.side} {t.quantity:g} {t.ticker}" for t in trades) + "."
        )
    if changes:
        parts.append(
            "Watchlist: " + ", ".join(f"{c.action} {c.ticker}" for c in changes) + "."
        )
    if not parts:
        if portfolio:
            parts.append(
                f"Your portfolio is worth ${portfolio['total_value']:,.2f} with "
                f"${portfolio['cash_balance']:,.2f} in cash and "
                f"{len(portfolio['positions'])} open position(s)."
            )
        else:
            parts.append("Hello! I'm FinAlly, your AI trading assistant.")
    return LLMResponse(message=" ".join(parts), trades=trades, watchlist_changes=changes)


# ---- Real client --------------------------------------------------------


def call_llm(messages: list[dict]) -> LLMResponse:
    """Call the model via LiteLLM → OpenRouter (Cerebras) with structured output. Blocking."""
    from litellm import completion  # imported lazily: heavy import, unused in mock mode

    response = completion(
        model=MODEL,
        messages=messages,
        response_format=LLMResponse,
        reasoning_effort="low",
        extra_body=EXTRA_BODY,
    )
    return parse_llm_response(response.choices[0].message.content)
