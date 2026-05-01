"""
modules/fuzzy_search.py
-----------------------
Module 2 & 3 — Fuzzy search engine with 3-algorithm blend.

Algorithms
----------
  token_set_ratio  weight 0.5  — word order irrelevant, partial overlap
  WRatio           weight 0.3  — typo tolerance
  partial_ratio    weight 0.2  — short query inside long string

The engine loads product data from SQLite (with brand & category names joined),
builds a normalised index in memory, and scores queries at runtime.

Two embedding/index modes
--------------------------
  manual   — call engine.rebuild() explicitly
  interval — a background thread rebuilds the index every N seconds
"""

import re
import threading
import time
import sys
import os
from typing import List, Dict, Any, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import SEARCH_MIN_SCORE, SEARCH_DEFAULT_K, SEARCH_MAX_K
from db.database import get_connection

try:
    from rapidfuzz import fuzz, process
    RAPIDFUZZ_AVAILABLE = True
except ImportError:
    RAPIDFUZZ_AVAILABLE = False


# ── Text normaliser ────────────────────────────────────────────────────────────

def normalize(text: str) -> str:
    """
    Prepare text for fuzzy matching:
      - lowercase
      - strip prices  ($20.00)
      - strip bracket content  (9MM)  [BOX]
      - keep only alphanumeric + spaces
      - collapse whitespace
    """
    if not text or not isinstance(text, str):
        return ""
    text = text.lower()
    text = re.sub(r'\$\s*\d+\.?\d*', ' ', text)        # prices
    text = re.sub(r'[\[\(\{].*?[\]\)\}]', ' ', text)   # brackets
    text = re.sub(r'[^a-z0-9\s]', ' ', text)           # special chars
    text = re.sub(r'\s+', ' ', text).strip()
    return text


# ── Core scoring ───────────────────────────────────────────────────────────────

def blend_score(query: str, normalized_text: str, raw_text: str) -> float:
    """
    Three-algorithm blend.  Scores against both normalised and raw text,
    returns the higher of the two.

    Weights:
        token_set_ratio  0.5
        WRatio           0.3
        partial_ratio    0.2
    """
    if not RAPIDFUZZ_AVAILABLE:
        raise RuntimeError("rapidfuzz is not installed. Run: pip install rapidfuzz")

    # Against normalised text
    ts_n = fuzz.token_set_ratio(query, normalized_text)
    wr_n = fuzz.WRatio(query, normalized_text)
    pr_n = fuzz.partial_ratio(query, normalized_text)
    score_n = 0.5 * ts_n + 0.3 * wr_n + 0.2 * pr_n

    # Against raw text (preserves numbers, original casing)
    ts_r = fuzz.token_set_ratio(query, raw_text)
    wr_r = fuzz.WRatio(query, raw_text)
    pr_r = fuzz.partial_ratio(query, raw_text)
    score_r = 0.5 * ts_r + 0.3 * wr_r + 0.2 * pr_r

    return max(score_n, score_r)


# ── Main engine ────────────────────────────────────────────────────────────────

class FuzzySearchEngine:
    """
    Fuzzy product search engine backed by SQLite.

    Parameters
    ----------
    text_fields : list of str
        Product dict fields to combine into the searchable string.
        Default: ["name", "brand_name", "category_name"]
    min_score : float
        Minimum blend score (0–100) to include in results.
    rebuild_interval : int or None
        If set, a background thread rebuilds the index every N seconds.
        If None, call rebuild() manually.
    """

    def __init__(
        self,
        text_fields: Optional[List[str]] = None,
        min_score: float = SEARCH_MIN_SCORE,
        rebuild_interval: Optional[int] = None,
    ):
        if not RAPIDFUZZ_AVAILABLE:
            raise RuntimeError("rapidfuzz is not installed. Run: pip install rapidfuzz")

        self.text_fields       = text_fields or ["name", "brand_name", "category_name"]
        self.min_score         = min_score
        self.rebuild_interval  = rebuild_interval

        self._items:            List[Dict[str, Any]] = []
        self._raw_strings:      List[str] = []
        self._normalized_strings: List[str] = []
        self._lock             = threading.RLock()
        self._last_built:      Optional[float] = None

        # Build index on startup
        self.rebuild()

        # Start background refresh thread if interval is set
        if rebuild_interval:
            self._start_background_refresh()

    # ── Index building ─────────────────────────────────────────────────────────

    def _load_products_from_db(self) -> List[Dict[str, Any]]:
        """
        Load products from SQLite, joining brand and category names.
        Only active, for-sale products are loaded.
        """
        conn = get_connection()
        try:
            rows = conn.execute(
                """
                SELECT
                    p.id,
                    p.name,
                    p.sku,
                    p.sku2,
                    p.item_code,
                    p.image,
                    p.main_image,
                    p.srp,
                    p.sales_price,
                    p.product_description,
                    p.aisle,
                    p.rack,
                    p.shelf,
                    p.bin,
                    p.qty_box,
                    p.case_qty,
                    p.out_of_stock,
                    p.is_inactive,
                    p.product_group_id,
                    p.group_variation_name,
                    COALESCE(b.name, '')  AS brand_name,
                    COALESCE(c.name, '')  AS category_name,
                    COALESCE(pg.name, '') AS group_name
                FROM products p
                LEFT JOIN brands       b  ON b.id  = p.brand_id
                LEFT JOIN categories   c  ON c.id  = p.category_id
                LEFT JOIN product_group pg ON pg.id = p.product_group_id
                WHERE p.is_inactive    = 0
                ORDER BY p.id
                """
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def rebuild(self) -> int:
        """
        Reload products from SQLite and rebuild the in-memory index.
        Thread-safe.  Returns number of products indexed.
        """
        items = self._load_products_from_db()

        raw_strings        = []
        normalized_strings = []

        for item in items:
            parts = [str(item.get(f, '') or '') for f in self.text_fields]
            raw   = ' '.join(p for p in parts if p and p.lower() not in ('nan', 'none', ''))
            raw_strings.append(raw)
            normalized_strings.append(normalize(raw))

        with self._lock:
            self._items              = items
            self._raw_strings        = raw_strings
            self._normalized_strings = normalized_strings
            self._last_built         = time.time()

        print(f"[Search] Index rebuilt — {len(items)} products loaded.")
        return len(items)

    def _start_background_refresh(self):
        """Start a daemon thread that calls rebuild() every N seconds."""
        def _worker():
            while True:
                time.sleep(self.rebuild_interval)
                try:
                    self.rebuild()
                except Exception as exc:
                    print(f"[Search] Background rebuild failed: {exc}")

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        print(f"[Search] Background index refresh every {self.rebuild_interval}s started.")

    # ── Search ─────────────────────────────────────────────────────────────────

    def search(self, query: str, top_k: int = SEARCH_DEFAULT_K) -> List[Dict[str, Any]]:
        """
        Search products.

        Returns list of product dicts enriched with:
            score       — blend score 0–100
            score_pct   — same value (for template display)
            score_label — "high" | "medium" | "low"
        Sorted by score descending.
        """
        top_k = min(top_k, SEARCH_MAX_K)
        query_n = normalize(query)
        if not query_n:
            return []

        with self._lock:
            items       = self._items
            raw_strs    = self._raw_strings
            norm_strs   = self._normalized_strings

        if not items:
            return []

        # Pass 1 — fast WRatio scan to get top candidates (2× top_k)
        fast_matches = process.extract(
            query_n,
            norm_strs,
            scorer=fuzz.WRatio,
            limit=top_k * 2,
        )

        # Pass 2 — full 3-way blend re-score
        results = []
        seen    = set()

        for _text, _fast_score, index in fast_matches:
            if index in seen:
                continue
            seen.add(index)

            score = blend_score(query_n, norm_strs[index], raw_strs[index])
            if score < self.min_score:
                continue

            result = dict(items[index])
            result["score"]       = round(score, 2)
            result["score_pct"]   = round(score, 2)
            result["score_label"] = self._label(score)
            results.append(result)

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]

    def search_with_field_scores(self, query: str, top_k: int = SEARCH_DEFAULT_K) -> List[Dict[str, Any]]:
        """
        Same as search() but also returns per-field scores.
        Useful for debugging and bucketing.
        """
        results  = self.search(query, top_k)
        query_n  = normalize(query)

        for r in results:
            r["field_scores"] = {
                field: round(
                    blend_score(
                        query_n,
                        normalize(str(r.get(field, '') or '')),
                        str(r.get(field, '') or '').lower(),
                    ),
                    2,
                )
                for field in self.text_fields
            }
        return results

    # ── Helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def _label(score: float) -> str:
        if score >= 70:
            return "high"
        if score >= 50:
            return "medium"
        if score >= 35:
            return "low"
        return "none"

    def stats(self) -> dict:
        with self._lock:
            return {
                "total_products": len(self._items),
                "last_built":     self._last_built,
                "min_score":      self.min_score,
                "text_fields":    self.text_fields,
            }


# ── Module-level singleton (lazy) ──────────────────────────────────────────────
_engine: Optional[FuzzySearchEngine] = None
_engine_lock = threading.Lock()


def get_engine(rebuild_interval: Optional[int] = None) -> FuzzySearchEngine:
    """
    Return the module-level singleton engine.
    Creates it on first call.

    Parameters
    ----------
    rebuild_interval : int or None
        Seconds between automatic index rebuilds.
        Pass 300 for a 5-minute refresh cycle.
    """
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                _engine = FuzzySearchEngine(rebuild_interval=rebuild_interval)
    return _engine
