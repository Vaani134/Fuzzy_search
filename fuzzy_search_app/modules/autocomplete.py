"""
modules/autocomplete.py
-----------------------
Fast SQLite-backed autocomplete / search suggestions.

Returns up to `limit` suggestions from:
  - product names
  - brand names
  - category names

Strategy: prefix match first (fastest), then LIKE fallback for mid-word.
Results are deduplicated and ranked: exact prefix > contains.
"""

import re
import sys
import os
from typing import List, Dict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db.database import get_connection


def _normalize(text: str) -> str:
    """Lowercase and collapse whitespace."""
    return re.sub(r"\s+", " ", text.lower().strip())


def get_suggestions(query: str, limit: int = 10) -> List[Dict]:
    """
    Return autocomplete suggestions for `query`.

    Each suggestion dict:
        text   — display text
        type   — "product" | "brand" | "category"
        id     — record id (for direct navigation on product suggestions)
    """
    query = query.strip()
    if not query or len(query) < 2:
        return []

    q_like_prefix  = query + "%"          # starts with
    q_like_contains = "%" + query + "%"   # contains anywhere

    conn = get_connection()
    results = []
    seen = set()

    try:
        # ── 1. Product names — prefix match (highest priority) ────────────────
        rows = conn.execute(
            """
            SELECT id, name, 1 AS priority
            FROM products
            WHERE name LIKE ? AND is_inactive = 0
            ORDER BY name
            LIMIT ?
            """,
            (q_like_prefix, limit),
        ).fetchall()

        for r in rows:
            key = ("product", r["name"].lower())
            if key not in seen:
                seen.add(key)
                results.append({"text": r["name"], "type": "product", "id": r["id"], "priority": 1})

        # ── 2. Brand names — prefix match ─────────────────────────────────────
        rows = conn.execute(
            """
            SELECT id, name
            FROM brands
            WHERE name LIKE ? AND deleted_at IS NULL
            ORDER BY name
            LIMIT ?
            """,
            (q_like_prefix, limit // 2),
        ).fetchall()

        for r in rows:
            key = ("brand", r["name"].lower())
            if key not in seen:
                seen.add(key)
                results.append({"text": r["name"], "type": "brand", "id": r["id"], "priority": 2})

        # ── 3. Category names — prefix match ──────────────────────────────────
        rows = conn.execute(
            """
            SELECT id, name
            FROM categories
            WHERE name LIKE ? AND deleted_at IS NULL
            ORDER BY name
            LIMIT ?
            """,
            (q_like_prefix, limit // 2),
        ).fetchall()

        for r in rows:
            key = ("category", r["name"].lower())
            if key not in seen:
                seen.add(key)
                results.append({"text": r["name"], "type": "category", "id": r["id"], "priority": 3})

        # ── 4. Fill remaining slots with contains-match on products ───────────
        if len(results) < limit:
            remaining = limit - len(results)
            rows = conn.execute(
                """
                SELECT id, name
                FROM products
                WHERE name LIKE ? AND name NOT LIKE ? AND is_inactive = 0
                ORDER BY name
                LIMIT ?
                """,
                (q_like_contains, q_like_prefix, remaining),
            ).fetchall()

            for r in rows:
                key = ("product", r["name"].lower())
                if key not in seen:
                    seen.add(key)
                    results.append({"text": r["name"], "type": "product", "id": r["id"], "priority": 4})

    finally:
        conn.close()

    # Sort: priority asc, then alphabetical
    results.sort(key=lambda x: (x["priority"], x["text"].lower()))
    return results[:limit]
