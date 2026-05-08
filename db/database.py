"""
db/database.py
--------------
SQLite connection helper.
Initialises the schema on first run.
"""

import sqlite3
import os
import sys

# Allow imports from parent directory
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import SQLITE_PATH, SCHEMA_PATH


def get_connection() -> sqlite3.Connection:
    """Return a SQLite connection with row_factory set to Row."""
    conn = sqlite3.connect(SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")   # better concurrent read performance
    # FK checks are enabled per-operation where needed; off by default for sync safety
    conn.execute("PRAGMA foreign_keys = OFF")
    return conn


def init_db() -> None:
    """
    Create tables from schema.sql if they don't exist yet.
    Also runs any incremental migrations for existing databases
    (e.g. adding the search_history table to older installs).
    """
    db_dir = os.path.dirname(SQLITE_PATH)

    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        sql = f.read()
    conn = get_connection()
    try:
        conn.executescript(sql)
        conn.commit()
        print(f"[DB] SQLite initialised at {SQLITE_PATH}")
    finally:
        conn.close()

    # ── Incremental migrations ─────────────────────────────────────────────────
    # These are idempotent — safe to run on every startup.
    _run_migrations()


def _run_migrations() -> None:
    """
    Apply schema additions that may be missing from older database files.
    Each migration is wrapped in a try/except so a single failure does not
    prevent the app from starting.

    SQLite does not support IF NOT EXISTS on ALTER TABLE, so we catch the
    OperationalError that fires when a column already exists and move on.
    """
    conn = get_connection()
    try:
        # ── Migration 1: search_history table (added in v2) ───────────────────
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS search_history (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                query        TEXT    NOT NULL,
                result_count INTEGER NOT NULL DEFAULT 0,
                timestamp    TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_search_history_query "
            "ON search_history(query)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_search_history_timestamp "
            "ON search_history(timestamp)"
        )

        # ── Migration 2: analytics columns (added in v3) ──────────────────────
        # is_zero_result — 1 when the search returned no results.
        # Existing rows default to 0 (unknown / assumed non-zero), which is
        # the safe backward-compatible value.
        _add_column_if_missing(
            conn,
            table="search_history",
            column="is_zero_result",
            definition="INTEGER NOT NULL DEFAULT 0",
        )

        # search_count — cumulative counter for repeated identical queries.
        # Existing rows default to 1 (each old row represents one search event).
        _add_column_if_missing(
            conn,
            table="search_history",
            column="search_count",
            definition="INTEGER NOT NULL DEFAULT 1",
        )

        # last_searched — timestamp of the most recent search for this query.
        # Existing rows default to their original timestamp so trending queries
        # computed over a 24-hour window degrade gracefully on old data.
        _add_column_if_missing(
            conn,
            table="search_history",
            column="last_searched",
            definition="TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP",
        )

        # Indexes for the new columns (CREATE INDEX IF NOT EXISTS is safe to
        # run repeatedly — SQLite ignores it when the index already exists).
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_search_history_zero_result "
            "ON search_history(is_zero_result)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_search_history_last_searched "
            "ON search_history(last_searched)"
        )

        conn.commit()

        # ── Migration 3: synonyms table (added in v4) ─────────────────────────
        # Created separately so the seed step can check whether the table was
        # just created (needs seeding) or already existed (skip seeding).
        _migrate_synonyms(conn)

        # ── Migration 4: synonym_suggestions table (added in v5) ──────────────
        _migrate_synonym_suggestions(conn)

        # ── Migration 5: top_score column on search_history (added in v6) ────
        # Existing rows default to 0.0 — safe backward-compatible value meaning
        # "score unknown".  The suggester treats 0.0 as weak (< 70 threshold).
        _add_column_if_missing(
            conn,
            table="search_history",
            column="top_score",
            definition="REAL NOT NULL DEFAULT 0.0",
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_search_history_top_score "
            "ON search_history(top_score)"
        )
        conn.commit()

        # ── Migration 6: product_clicks table (added in v7) ───────────────────
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS product_clicks (
                product_id  INTEGER PRIMARY KEY REFERENCES products(id) ON DELETE CASCADE,
                click_count INTEGER NOT NULL DEFAULT 0,
                updated_at  TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_product_clicks_count "
            "ON product_clicks(click_count)"
        )
        conn.commit()

    except Exception as exc:
        print(f"[DB] Migration warning: {exc}")
    finally:
        conn.close()


# ── Default synonyms seeded into the DB on first run ──────────────────────────
# Mirrors the old hardcoded SYNONYMS dict in modules/fuzzy_search.py.
# These are only inserted when the synonyms table is first created — existing
# rows are never overwritten, so user edits via the API are preserved.
_DEFAULT_SYNONYMS = [
    ("hooka",    "hookah"),
    ("hokkah",   "hookah"),
    ("sheesha",  "hookah"),
    ("shisha",   "hookah"),
    ("narghile", "hookah"),
    ("nargile",  "hookah"),
    ("grider",   "grinder"),
    ("griders",  "grinders"),
    ("cigartte", "cigarette"),
    ("cigaret",  "cigarette"),
    ("cigaretts","cigarettes"),
    ("vap",      "vape"),
    ("ecig",     "e-cigarette"),
    ("e cig",    "e-cigarette"),
    ("enrgy",    "energy"),
    ("liter",    "lighter"),
    ("litre",    "lighter"),
    ("pip",      "pipe"),
    ("tobaco",   "tobacco"),
    ("tobcco",   "tobacco"),
    ("charcol",  "charcoal"),
    ("charcole", "charcoal"),
    ("blunt",    "blunt wrap"),
    ("wraps",    "wrap"),
]


def _migrate_synonyms(conn: sqlite3.Connection) -> None:
    """
    Create the synonyms table if it doesn't exist, then seed default rows.
    Uses INSERT OR IGNORE so existing user-added synonyms are never touched.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS synonyms (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            variant    TEXT    NOT NULL,
            canonical  TEXT    NOT NULL,
            created_at TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(variant)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_synonyms_variant "
        "ON synonyms(variant)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_synonyms_canonical "
        "ON synonyms(canonical)"
    )

    # INSERT OR IGNORE: only inserts rows whose variant doesn't exist yet.
    # Safe to run on every startup — never overwrites user edits.
    conn.executemany(
        "INSERT OR IGNORE INTO synonyms (variant, canonical) VALUES (?, ?)",
        _DEFAULT_SYNONYMS,
    )
    conn.commit()
    print(f"[DB] Synonyms table ready ({len(_DEFAULT_SYNONYMS)} defaults seeded if new).")


def _migrate_synonym_suggestions(conn: sqlite3.Connection) -> None:
    """
    Create the synonym_suggestions table if it doesn't exist.
    Idempotent — safe to run on every startup.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS synonym_suggestions (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            variant    TEXT    NOT NULL,
            canonical  TEXT    NOT NULL,
            score      REAL    NOT NULL,
            status     TEXT    NOT NULL DEFAULT 'pending',
            created_at TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(variant)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_syn_sug_status  "
        "ON synonym_suggestions(status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_syn_sug_variant "
        "ON synonym_suggestions(variant)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_syn_sug_score   "
        "ON synonym_suggestions(score)"
    )
    conn.commit()
    print("[DB] synonym_suggestions table ready.")


def _add_column_if_missing(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    definition: str,
) -> None:
    """
    Add *column* to *table* only if it does not already exist.

    SQLite raises ``OperationalError: duplicate column name`` when you
    ALTER TABLE ADD COLUMN on an existing column.  We catch that specific
    error and treat it as a no-op so migrations are fully idempotent.

    Parameters
    ----------
    conn       : open SQLite connection
    table      : table name
    column     : column name to add
    definition : SQL type + constraints, e.g. "INTEGER NOT NULL DEFAULT 0"
    """
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    except sqlite3.OperationalError as exc:
        # "duplicate column name: <column>" means it already exists — safe to ignore.
        if "duplicate column name" not in str(exc).lower():
            raise  # re-raise anything unexpected


def dict_from_row(row: sqlite3.Row) -> dict:
    """Convert a sqlite3.Row to a plain dict."""
    return dict(row)
