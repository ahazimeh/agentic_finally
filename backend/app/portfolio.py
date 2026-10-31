"""Portfolio logic: valuation, trade execution, snapshots."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .database import DEFAULT_USER_ID, Database, new_id, now_iso
from .market import PriceCache

# Quantities below this are treated as zero (fractional-share float noise).
EPSILON = 1e-9

TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


class TradeError(ValueError):
    """Raised when a trade fails validation."""


def normalize_ticker(ticker: str) -> str:
    """Upper-case and validate a ticker symbol. Raises ValueError if invalid."""
    t = (ticker or "").strip().upper()
    if not TICKER_RE.match(t):
        raise ValueError(f"Invalid ticker symbol: {ticker!r}")
    return t


@dataclass(frozen=True)
class TradeResult:
    ticker: str
    side: str
    quantity: float
    price: float
    cash_balance: float
    position_quantity: float

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "side": self.side,
            "quantity": self.quantity,
            "price": self.price,
            "total": round(self.quantity * self.price, 2),
            "cash_balance": round(self.cash_balance, 2),
            "position_quantity": self.position_quantity,
        }


def _pct(numerator: float, denominator: float) -> float:
    return round(numerator / denominator * 100, 2) if denominator else 0.0


def get_cash(conn, user_id: str = DEFAULT_USER_ID) -> float:
    row = conn.execute("SELECT cash_balance FROM users_profile WHERE id = ?", (user_id,)).fetchone()
    return float(row["cash_balance"]) if row else 0.0


def get_position_tickers(db: Database, user_id: str = DEFAULT_USER_ID) -> list[str]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT ticker FROM positions WHERE user_id = ? ORDER BY ticker", (user_id,)
        ).fetchall()
    return [r["ticker"] for r in rows]


def get_portfolio(db: Database, cache: PriceCache, user_id: str = DEFAULT_USER_ID) -> dict:
    """Current cash, positions valued at live prices, totals and unrealized P&L.

    If no live price is known for a held ticker, it is valued at average cost.
    """
    with db.connect() as conn:
        cash = get_cash(conn, user_id)
        rows = conn.execute(
            "SELECT ticker, quantity, avg_cost FROM positions WHERE user_id = ? ORDER BY ticker",
            (user_id,),
        ).fetchall()

    positions = []
    positions_value = 0.0
    cost_basis_total = 0.0
    for r in rows:
        qty = float(r["quantity"])
        avg_cost = float(r["avg_cost"])
        live = cache.get_price(r["ticker"])
        price = live if live is not None else avg_cost
        market_value = qty * price
        cost_basis = qty * avg_cost
        pnl = market_value - cost_basis
        positions_value += market_value
        cost_basis_total += cost_basis
        positions.append(
            {
                "ticker": r["ticker"],
                "quantity": qty,
                "avg_cost": round(avg_cost, 4),
                "current_price": round(price, 4),
                "market_value": round(market_value, 2),
                "cost_basis": round(cost_basis, 2),
                "unrealized_pnl": round(pnl, 2),
                "unrealized_pnl_percent": _pct(pnl, cost_basis),
            }
        )

    total_value = cash + positions_value
    for p in positions:
        p["weight"] = _pct(p["market_value"], positions_value)

    unrealized = positions_value - cost_basis_total
    return {
        "cash_balance": round(cash, 2),
        "positions_value": round(positions_value, 2),
        "total_value": round(total_value, 2),
        "unrealized_pnl": round(unrealized, 2),
        "unrealized_pnl_percent": _pct(unrealized, cost_basis_total),
        "positions": positions,
    }


def execute_trade(
    db: Database,
    cache: PriceCache,
    ticker: str,
    side: str,
    quantity: float,
    user_id: str = DEFAULT_USER_ID,
) -> TradeResult:
    """Execute a market order at the current cached price.

    Raises TradeError on validation failure (bad input, no price,
    insufficient cash, insufficient shares). All DB changes are atomic.
    """
    try:
        ticker = normalize_ticker(ticker)
    except ValueError as e:
        raise TradeError(str(e)) from e
    side = (side or "").strip().lower()
    if side not in ("buy", "sell"):
        raise TradeError(f"Invalid side {side!r}: must be 'buy' or 'sell'")
    try:
        quantity = float(quantity)
    except (TypeError, ValueError) as e:
        raise TradeError("Quantity must be a number") from e
    if not quantity > 0 or quantity != quantity or quantity == float("inf"):
        raise TradeError("Quantity must be a positive number")

    price = cache.get_price(ticker)
    if price is None:
        raise TradeError(f"No price available for {ticker}; add it to the watchlist first")

    ts = now_iso()
    with db.connect() as conn:
        cash = get_cash(conn, user_id)
        row = conn.execute(
            "SELECT quantity, avg_cost FROM positions WHERE user_id = ? AND ticker = ?",
            (user_id, ticker),
        ).fetchone()
        held = float(row["quantity"]) if row else 0.0
        avg_cost = float(row["avg_cost"]) if row else 0.0
        cost = quantity * price

        if side == "buy":
            if cost > cash + EPSILON:
                raise TradeError(
                    f"Insufficient cash: buying {quantity:g} {ticker} costs ${cost:,.2f}, "
                    f"available ${cash:,.2f}"
                )
            new_qty = held + quantity
            new_avg = (held * avg_cost + cost) / new_qty
            new_cash = cash - cost
        else:
            if quantity > held + EPSILON:
                raise TradeError(
                    f"Insufficient shares: trying to sell {quantity:g} {ticker}, own {held:g}"
                )
            new_qty = held - quantity
            new_avg = avg_cost
            new_cash = cash + cost

        conn.execute(
            "UPDATE users_profile SET cash_balance = ? WHERE id = ?", (new_cash, user_id)
        )
        if new_qty <= EPSILON:
            new_qty = 0.0
            conn.execute(
                "DELETE FROM positions WHERE user_id = ? AND ticker = ?", (user_id, ticker)
            )
        elif row:
            conn.execute(
                "UPDATE positions SET quantity = ?, avg_cost = ?, updated_at = ? "
                "WHERE user_id = ? AND ticker = ?",
                (new_qty, new_avg, ts, user_id, ticker),
            )
        else:
            conn.execute(
                "INSERT INTO positions (id, user_id, ticker, quantity, avg_cost, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (new_id(), user_id, ticker, new_qty, new_avg, ts),
            )
        conn.execute(
            "INSERT INTO trades (id, user_id, ticker, side, quantity, price, executed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (new_id(), user_id, ticker, side, quantity, price, ts),
        )

    return TradeResult(
        ticker=ticker,
        side=side,
        quantity=quantity,
        price=price,
        cash_balance=new_cash,
        position_quantity=new_qty,
    )


def record_snapshot(db: Database, cache: PriceCache, user_id: str = DEFAULT_USER_ID) -> float:
    """Persist the current total portfolio value. Returns the recorded value."""
    total = get_portfolio(db, cache, user_id)["total_value"]
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO portfolio_snapshots (id, user_id, total_value, recorded_at) "
            "VALUES (?, ?, ?, ?)",
            (new_id(), user_id, total, now_iso()),
        )
    return total


def get_history(db: Database, user_id: str = DEFAULT_USER_ID, limit: int = 2000) -> list[dict]:
    """Snapshots in chronological order (most recent `limit` entries)."""
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT total_value, recorded_at FROM ("
            "  SELECT total_value, recorded_at, rowid AS rid FROM portfolio_snapshots"
            "  WHERE user_id = ? ORDER BY rid DESC LIMIT ?"
            ") ORDER BY rid ASC",
            (user_id, limit),
        ).fetchall()
    return [{"total_value": r["total_value"], "recorded_at": r["recorded_at"]} for r in rows]
