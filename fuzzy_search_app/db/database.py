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
    """Create tables from schema.sql if they don't exist yet."""
    os.makedirs(os.path.dirname(SQLITE_PATH), exist_ok=True)
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        sql = f.read()
    conn = get_connection()
    try:
        conn.executescript(sql)
        conn.commit()
        print(f"[DB] SQLite initialised at {SQLITE_PATH}")
    finally:
        conn.close()


def dict_from_row(row: sqlite3.Row) -> dict:
    """Convert a sqlite3.Row to a plain dict."""
    return dict(row)
