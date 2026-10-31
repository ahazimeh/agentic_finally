"""Lazy initialization and seed data."""

from app.database import Database


def test_creates_file_and_seeds(tmp_path):
    path = tmp_path / "nested" / "finally.db"
    db = Database(path)
    assert not path.exists()
    with db.connect() as conn:
        cash = conn.execute("SELECT cash_balance FROM users_profile WHERE id='default'").fetchone()
        tickers = [r["ticker"] for r in conn.execute("SELECT ticker FROM watchlist ORDER BY rowid")]
    assert path.exists()
    assert cash["cash_balance"] == 10000.0
    assert tickers == ["AAPL", "GOOGL", "MSFT", "AMZN", "TSLA", "NVDA", "META", "JPM", "V", "NFLX"]


def test_all_tables_exist(db):
    with db.connect() as conn:
        names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {
        "users_profile", "watchlist", "positions", "trades", "portfolio_snapshots", "chat_messages"
    } <= names


def test_reinitialize_does_not_reseed(tmp_path):
    path = tmp_path / "finally.db"
    db = Database(path)
    with db.connect() as conn:
        conn.execute("DELETE FROM watchlist WHERE ticker = 'NFLX'")
        conn.execute("UPDATE users_profile SET cash_balance = 5 WHERE id = 'default'")
    db2 = Database(path)  # simulates a restart
    with db2.connect() as conn:
        count = conn.execute("SELECT COUNT(*) AS n FROM watchlist").fetchone()["n"]
        cash = conn.execute("SELECT cash_balance FROM users_profile").fetchone()["cash_balance"]
    assert count == 9
    assert cash == 5


def test_recreates_deleted_file(tmp_path):
    path = tmp_path / "finally.db"
    db = Database(path)
    with db.connect():
        pass
    path.unlink()
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM watchlist").fetchone()["n"] == 10


def test_watchlist_unique_constraint(db):
    import sqlite3

    import pytest

    with pytest.raises(sqlite3.IntegrityError):
        with db.connect() as conn:
            conn.execute(
                "INSERT INTO watchlist (id, user_id, ticker, added_at) VALUES ('x','default','AAPL','t')"
            )
