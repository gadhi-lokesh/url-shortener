"""
database.py — SQLite initialisation and per-request connection management.

Uses Flask's application-context `g` so each request gets exactly one
connection, which is closed cleanly when the request tears down.

In-memory databases (:memory:)
-------------------------------
Each call to sqlite3.connect(":memory:") returns a brand-new, empty
database.  For test isolation we therefore attach a single persistent
connection to the Flask app object (_app._db_conn) and reuse it for every
request context.  The tables created in init_db() are visible to all
subsequent get_db() calls because they all share that same connection.

Schema
------
links
    id          INTEGER PK AUTOINCREMENT
    short_code  TEXT    UNIQUE NOT NULL   -- the slug, e.g. "aB3xYz"
    original_url TEXT   NOT NULL          -- the full destination URL
    created_at  TEXT    NOT NULL          -- ISO-8601 UTC datetime
    expires_at  TEXT                      -- ISO-8601 UTC datetime, NULL = never
    click_count INTEGER NOT NULL DEFAULT 0
    is_active   INTEGER NOT NULL DEFAULT 1 -- 0 = soft-deleted

Indexes
-------
idx_short_code on links(short_code)   -- fast O(log n) redirect lookups
"""

import sqlite3
import os

from flask import g, current_app


# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------

def _make_connection(path: str) -> sqlite3.Connection:
    """Open and configure a new SQLite connection."""
    conn = sqlite3.connect(path, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def get_db() -> sqlite3.Connection:
    """
    Return (or create) the per-request SQLite connection stored in Flask g.

    For in-memory databases a single persistent connection is stored on the
    app object so all requests see the same tables.
    """
    if "db" not in g:
        db_path = current_app.config["DATABASE"]
        if db_path == ":memory:":
            # Reuse the single shared connection created during init_db().
            g.db = current_app._db_conn  # type: ignore[attr-defined]
        else:
            g.db = _make_connection(db_path)
    return g.db


def close_db(error=None) -> None:
    """
    Teardown per-request connection.

    For in-memory databases we only remove it from g (not close it) because
    the underlying connection is shared across requests.
    """
    db_path = current_app.config["DATABASE"]
    db = g.pop("db", None)
    if db is not None and db_path != ":memory:":
        db.close()


# ---------------------------------------------------------------------------
# Schema DDL
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS links (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    short_code   TEXT    NOT NULL UNIQUE,
    original_url TEXT    NOT NULL,
    created_at   TEXT    NOT NULL,
    expires_at   TEXT,
    click_count  INTEGER NOT NULL DEFAULT 0,
    is_active    INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_short_code
    ON links (short_code);

CREATE INDEX IF NOT EXISTS idx_active_code
    ON links (short_code, is_active);
"""


# ---------------------------------------------------------------------------
# Schema initialisation
# ---------------------------------------------------------------------------

def init_db(app) -> None:
    """
    Create the database file and apply the schema (idempotent).
    Called once from create_app() at startup.
    """
    db_path = app.config["DATABASE"]

    if db_path == ":memory:":
        # Create a single persistent connection and store it on the app so
        # every subsequent get_db() call can reuse it.
        conn = _make_connection(":memory:")
        conn.executescript(_DDL)
        conn.commit()
        app._db_conn = conn  # type: ignore[attr-defined]
    else:
        # File-based: make sure the parent directory exists.
        parent_dir = os.path.dirname(db_path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
        conn = _make_connection(db_path)
        try:
            conn.executescript(_DDL)
            conn.commit()
        finally:
            conn.close()
