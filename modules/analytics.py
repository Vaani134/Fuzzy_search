"""
modules/analytics.py
--------------------
Search history & analytics.

Responsibilities
----------------
  log_search()              — record every search event; increment counter on repeats
  get_recent_searches()     — most recent N raw search events
  get_top_queries()         — most-searched queries by cumulative search_count
  get_zero_result_queries() — queries that consistently return no results
  get_trending_queries()    — queries searched most in the last 24 hours

Table: search_history
---------------------
  id              INTEGER  PK
  query           TEXT     normalised query string (lowercase, stripped)
  result_count    INTEGER  results returned for this event
  timestamp       TEXT     ISO-8601 UTC — when this row was first created
  is_zero_result  INTEGER  1 if result_count = 0, else 0
  search_count    INTEGER  cumulative count of times this query was searched
  last_searched   TEXT     ISO-8601 UTC — updated on every repeat search
  top_score       REAL     fuzzy score of the best result (0–100); 0.0 = no results

Design: one row per unique query (upsert pattern)
-------------------------------------------------
Rather than inserting a new row for every search event, log_search() uses
INSERT OR IGNORE + UPDATE so each unique query has exactly one row.  This
keeps the table compact and makes search_count / last_searched trivially
queryable without GROUP BY aggregation on every request.

Backward compatibility
----------------------
Old rows (created before v3) have is_zero_result=0, search_count=1, and
last_searched=CURRENT_TIMESTAMP (set by the migration default).  They are
treated as single-event rows and will be updated correctly on the next
search for the same query.

Old rows (created before v6) have top_score=0.0 (migration default).  The
synonym suggester treats 0.0 as a weak score (< 70), so old rows are
conservatively included in suggestion candidates — the worst outcome is a
few extra suggestions for the admin to review.
"""

import sys
import os
from datetime import datetime, timezone, timedelta
from typing import List, Dict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db.database import get_connection


# ── Internal helper ────────────────────────────────────────────────────────────

def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


# ── Public API ─────────────────────────────────────────────────────────────────

def log_search(query: str, result_count: int, top_score: float = 0.0) -> None:
    """
    Record a search event.

    Backward compatible — existing callers that pass only (query, result_count)
    continue to work; top_score defaults to 0.0 (treated as weak by the
    synonym suggester, which is the safe conservative choice).

    Behaviour
    ---------
    • First time a query is seen  → INSERT a new row (search_count = 1).
    • Query seen again            → UPDATE: increment search_count,
                                    refresh last_searched, update result_count,
                                    is_zero_result, and top_score to reflect
                                    the latest search outcome.

    Parameters
    ----------
    query        : raw user query string (will be lowercased + stripped)
    result_count : number of results the search returned
    top_score    : fuzzy score of the best result (0–100); 0.0 when no results.
                   Used by the synonym suggester to detect low-confidence queries.
    """
    if not query or not query.strip():
        return

    normalised = query.strip().lower()
    is_zero    = 1 if result_count == 0 else 0
    score      = round(float(top_score), 2)
    now        = _now_iso()

    conn = get_connection()
    try:
        # Step 1 — ensure a row exists for this query.
        conn.execute(
            """
            INSERT OR IGNORE INTO search_history
                (query, result_count, timestamp, is_zero_result,
                 search_count, last_searched, top_score)
            VALUES
                (?,     ?,            ?,         ?,
                 1,            ?,            ?)
            """,
            (normalised, result_count, now, is_zero, now, score),
        )

        # Step 2 — update mutable fields on repeat searches.
        # WHERE timestamp != ? prevents double-incrementing a freshly inserted row.
        conn.execute(
            """
            UPDATE search_history
            SET
                search_count   = search_count + 1,
                result_count   = ?,
                is_zero_result = ?,
                last_searched  = ?,
                top_score      = ?
            WHERE query = ?
              AND timestamp != ?
            """,
            (result_count, is_zero, now, score, normalised, now),
        )

        conn.commit()
    except Exception as exc:
        # Analytics must never break the main search flow.
        print(f"[Analytics] Failed to log search: {exc}")
    finally:
        conn.close()


def get_recent_searches(limit: int = 10) -> List[Dict]:
    """
    Return the most recently searched queries, ordered by last_searched desc.

    Returns
    -------
    list of dicts:
        id, query, result_count, timestamp, is_zero_result,
        search_count, last_searched, top_score
    """
    limit = min(max(1, limit), 100)
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT id, query, result_count, timestamp,
                   is_zero_result, search_count, last_searched, top_score
            FROM search_history
            ORDER BY last_searched DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_top_queries(limit: int = 10) -> List[Dict]:
    """
    Return the most-searched queries ranked by cumulative search_count.

    Uses the pre-aggregated search_count column — no GROUP BY needed,
    so this is an O(log n) index scan rather than a full table scan.

    Returns
    -------
    list of dicts: {query, search_count, result_count, is_zero_result}
    Sorted by search_count descending.
    """
    limit = min(max(1, limit), 100)
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT query, search_count, result_count, is_zero_result
            FROM search_history
            ORDER BY search_count DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_zero_result_queries(limit: int = 10) -> List[Dict]:
    """
    Return queries that currently return zero results, ranked by how
    frequently they have been searched (search_count desc).

    These are the highest-value gaps in the product catalog — queries
    users keep trying that the engine cannot satisfy.

    Returns
    -------
    list of dicts: {query, search_count, last_searched}
    Sorted by search_count descending (most-attempted zero-result queries first).
    """
    limit = min(max(1, limit), 100)
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT query, search_count, last_searched
            FROM search_history
            WHERE is_zero_result = 1
            ORDER BY search_count DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_trending_queries(hours: int = 24, limit: int = 10) -> List[Dict]:
    """
    Return queries searched most frequently within the last *hours* hours,
    ranked by search_count descending.

    "Trending" is defined as: queries whose last_searched timestamp falls
    within the rolling window AND have the highest search_count.  This is
    a lightweight proxy for trending — it surfaces queries that are both
    recent and frequently repeated.

    Parameters
    ----------
    hours : int
        Width of the rolling time window in hours.  Default: 24.
    limit : int
        Maximum number of results to return.  Default: 10, max: 100.

    Returns
    -------
    list of dicts: {query, search_count, result_count, last_searched}
    Sorted by search_count descending.
    """
    hours = max(1, hours)
    limit = min(max(1, limit), 100)

    # Compute the window start as an ISO-8601 UTC string.
    # SQLite stores timestamps as TEXT; lexicographic comparison works
    # correctly for ISO-8601 strings (YYYY-MM-DDTHH:MM:SS+00:00).
    window_start = (
        datetime.now(timezone.utc) - timedelta(hours=hours)
    ).isoformat()

    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT query, search_count, result_count, last_searched
            FROM search_history
            WHERE last_searched >= ?
            ORDER BY search_count DESC
            LIMIT ?
            """,
            (window_start, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
