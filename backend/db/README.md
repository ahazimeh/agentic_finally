# backend/db

SQL definitions used by `app/database.py` for lazy initialization.

- `schema.sql` — `CREATE TABLE IF NOT EXISTS` statements, executed on every startup.
- `seed.sql` — default user ($10,000 cash) + 10-ticker watchlist, applied only when the
  default user profile is missing (fresh database).

The SQLite file itself lives at `<repo>/db/finally.db` by default (override with the
`FINALLY_DB_PATH` env var). In Docker, mount the volume at `/app/db` and place the backend at
`/app/backend` so this directory is not shadowed by the volume.
