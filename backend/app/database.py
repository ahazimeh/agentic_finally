"""SQLite access with lazy schema creation and seeding.

The schema and seed SQL live in ``backend/db/``. The database file defaults to
``<repo root>/db/finally.db`` and can be overridden with ``FINALLY_DB_PATH``.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_USER_ID = "default"

BACKEND_DIR = Path(__file__).resolve().parents[1]
SQL_DIR = BACKEND_DIR / "db"
REPO_ROOT = BACKEND_DIR.parent
DEFAULT_DB_PATH = REPO_ROOT / "db" / "finally.db"


def now_iso() -> str:
    """Current UTC time as ISO-8601 with millisecond precision, e.g. 2026-01-01T12:00:00.123Z.

    Matches the format used by seed.sql (strftime('%Y-%m-%dT%H:%M:%fZ')).
    """
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_id() -> str:
    return str(uuid.uuid4())


def resolve_db_path() -> Path:
    env_path = os.environ.get("FINALLY_DB_PATH", "").strip()
    return Path(env_path) if env_path else DEFAULT_DB_PATH


class Database:
    """Thin wrapper around a SQLite file that initializes itself on first use."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else resolve_db_path()
        self._initialized = False
        self._init_lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def initialize(self) -> None:
        """Create tables and seed default data if needed. Idempotent."""
        with self._init_lock:
            if self._initialized and self.path.exists():
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = self._connect()
            try:
                conn.executescript((SQL_DIR / "schema.sql").read_text())
                has_user = conn.execute(
                    "SELECT 1 FROM users_profile WHERE id = ?", (DEFAULT_USER_ID,)
                ).fetchone()
                if not has_user:
                    logger.info("Seeding fresh database at %s", self.path)
                    conn.executescript((SQL_DIR / "seed.sql").read_text())
                conn.commit()
            finally:
                conn.close()
            self._initialized = True

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Yield a connection inside a transaction (commit on success, rollback on error)."""
        self.initialize()
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
