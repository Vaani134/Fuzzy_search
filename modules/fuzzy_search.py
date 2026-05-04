"""
modules/fuzzy_search.py
-----------------------
Core fuzzy search engine with:
  - 3-algorithm blend (token_set_ratio, WRatio, partial_ratio)
  - Synonym normalisation before scoring
  - Score boosting (exact match, startswith)
  - In-memory product index rebuilt from SQLite
  - Optional background auto-rebuild thread

Algorithms & weights
--------------------
  token_set_ratio  0.5  — word order irrelevant, partial overlap
  WRatio           0.3  — typo tolerance
  partial_ratio    0.2  — short query inside long string

Boosting rules (applied on top of blend score, capped at 100)
--------------------------------------------------------------
  Exact match (normalised)   → +20
  Starts-with match          → +10
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


# ── Synonym dictionary ─────────────────────────────────────────────────────────
# Maps user-typed variants → canonical search term.
# Keys are lowercase; values are the canonical form used for scoring.
# Add new synonyms here — no code changes needed elsewhere.
SYNONYMS: Dict[str, str] = {
    # Hookah / shisha variants
    "hooka":    "hookah",
    "hokkah":   "hookah",
    "sheesha":  "hookah",
    "shisha":   "hookah",
    "narghile": "hookah",
    "nargile":  "hookah",
    # Grinder variants
    "grider":   "grinder",
    "griders":  "grinders",
    # Cigarette variants
    "cigartte": "cigarette",
    "cigaret":  "cigarette",
    "cigaretts":"cigarettes",
    # Vape / e-cig
    "vap":      "vape",
    "ecig":     "e-cigarette",
    "e cig":    "e-cigarette",
    # Energy drink
    "enrgy":    "energy",
    # Lighter
    "liter":    "lighter",
    "litre":    "lighter",
    # Pipe
    "pip":      "pipe",
    # Tobacco
    "tobaco":   "tobacco",
    "tobcco":   "tobacco",
    # Charcoal
    "charcol":  "charcoal",
    "charcole": "charcoal",
    # Blunt / wrap
    "blunt":    "blunt wrap",
    "wraps":    "wrap",
}


def _build_synonym_regex() -> tuple:
    """
    Pre-compile a single regex that matches ALL synonym keys in one pass.

    Why one combined pattern instead of a loop?
    --------------------------------------------
    A loop applies replacements sequentially, so a later synonym can
    accidentally match text that was just written by an earlier one.
    Example with a loop:
        "sheesha" → "hookah"          (first replacement)
        "hooka"   matches in "hookah" → "hookaah"  (second replacement, wrong!)

    A single alternation pattern is applied in ONE scan of the string.
    The regex engine advances past each match without revisiting it, so
    no replacement can ever be re-processed.

    Construction rules:
      1. Sort keys by descending length so longer phrases (e.g. "e cig")
         are tried before shorter ones (e.g. "ecig") at each position.
      2. Wrap each key with \b word boundaries to block partial matches
         ("pip" must not fire inside "pipe").
      3. Compile with re.IGNORECASE so the match is case-insensitive while
         the replacement is always the canonical lowercase form.

    Returns
    -------
    (compiled_pattern, lookup_dict)
        lookup_dict maps lowercase variant → canonical replacement,
        used by the substitution callback.
    """
    # Sort longest key first — regex alternation is left-to-right greedy,
    # so the longer option must appear first to win at any given position.
    sorted_variants = sorted(SYNONYMS.keys(), key=len, reverse=True)

    # Build alternation: \b(variant1|variant2|...)\b
    alternation = "|".join(re.escape(v) for v in sorted_variants)
    pattern = re.compile(r"\b(?:" + alternation + r")\b", re.IGNORECASE)

    # Lowercase lookup so the callback can resolve any case variant
    lookup = {k.lower(): v for k, v in SYNONYMS.items()}

    return pattern, lookup


# Compile once at import time — not on every function call.
_SYNONYM_PATTERN, _SYNONYM_LOOKUP = _build_synonym_regex()


def apply_synonyms(query: str) -> str:
    """
    Expand known synonym variants in *query* to their canonical forms.

    Algorithm
    ---------
    Uses a single pre-compiled regex alternation so the entire string is
    scanned exactly once.  Each matched token is replaced via a lookup
    callback; unmatched text is passed through unchanged.

    Properties guaranteed by this implementation:
      • Word-boundary safety  — "pip" never fires inside "pipe"
      • No double replacement — "sheesha" → "hookah", not "hookaah"
      • Longest-match first   — "e cig" wins over "ecig" at the same position
      • Case-insensitive      — "Sheesha", "SHEESHA", "sheesha" all expand
      • Original spacing kept — only the matched token is replaced

    Parameters
    ----------
    query : str
        Raw user input, any casing.

    Returns
    -------
    str
        Query with synonym variants replaced by canonical terms.
        Identical to input if no synonyms are found.

    Examples
    --------
    >>> apply_synonyms("sheesha pipe")
    'hookah pipe'
    >>> apply_synonyms("hooka")
    'hookah'
    >>> apply_synonyms("glass pipe")
    'glass pipe'
    >>> apply_synonyms("SHISHA grider")
    'hookah grinder'
    """
    q = query.lower().strip()
    if not q:
        return q

    # re.sub with a callable replacement:
    #   match.group(0) is the exact text that was matched (lowercased via q).
    #   _SYNONYM_LOOKUP[matched_text] returns the canonical replacement.
    #   The regex engine advances past each match, so no position is visited twice.
    return _SYNONYM_PATTERN.sub(
        lambda m: _SYNONYM_LOOKUP[m.group(0).lower()],
        q,
    )


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


def apply_boost(base_score: float, query_n: str, product_name_n: str) -> float:
    """
    Apply deterministic boosting rules on top of the blend score.

    Rules (applied in order, cumulative):
      +20  exact match on normalised product name
      +10  product name starts with the query

    The final score is capped at 100.

    Parameters
    ----------
    base_score     : blend score (0–100)
    query_n        : normalised query string
    product_name_n : normalised product name
    """
    boost = 0.0

    if query_n and product_name_n:
        if query_n == product_name_n:
            boost += 20.0
        elif product_name_n.startswith(query_n):
            boost += 10.0

    return min(base_score + boost, 100.0)


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

        self._items:              List[Dict[str, Any]] = []
        self._raw_strings:        List[str] = []
        self._normalized_strings: List[str] = []
        self._lock                = threading.RLock()
        self._last_built:         Optional[float] = None

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
                    p.category_id,
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

    def search(
        self,
        query: str,
        top_k: int = SEARCH_DEFAULT_K,
        filters: Optional[Dict] = None,
    ) -> List[Dict[str, Any]]:
        """
        Search products with optional pre-filtering.

        Filters applied BEFORE fuzzy scoring (on the in-memory index):
            category   : str  — exact category_name match (case-insensitive)
            min_price  : float — minimum sales_price or srp
            max_price  : float — maximum sales_price or srp

        Returns list of product dicts enriched with:
            score       — boosted blend score 0–100
            score_pct   — same value (for template display)
            score_label — "high" | "medium" | "low"
        Sorted by score descending.
        """
        top_k = min(top_k, SEARCH_MAX_K)

        # Apply synonym expansion before normalising
        expanded_query = apply_synonyms(query)
        query_n = normalize(expanded_query)
        if not query_n:
            return []

        with self._lock:
            items     = self._items
            raw_strs  = self._raw_strings
            norm_strs = self._normalized_strings

        if not items:
            return []

        # ── Pre-filter: apply category / price filters on the index ───────────
        filters = filters or {}
        category_filter  = (filters.get("category") or "").strip().lower()
        min_price_filter = filters.get("min_price")
        max_price_filter = filters.get("max_price")

        if category_filter or min_price_filter is not None or max_price_filter is not None:
            filtered_indices = []
            for i, item in enumerate(items):
                # Category filter
                if category_filter:
                    item_cat = (item.get("category_name") or "").lower()
                    if category_filter not in item_cat:
                        continue
                # Price filter — use sales_price if available, else srp
                price = item.get("sales_price") or item.get("srp")
                if min_price_filter is not None and (price is None or price < min_price_filter):
                    continue
                if max_price_filter is not None and (price is None or price > max_price_filter):
                    continue
                filtered_indices.append(i)

            # Build filtered sub-lists for scoring
            f_items     = [items[i]     for i in filtered_indices]
            f_raw_strs  = [raw_strs[i]  for i in filtered_indices]
            f_norm_strs = [norm_strs[i] for i in filtered_indices]
        else:
            f_items     = items
            f_raw_strs  = raw_strs
            f_norm_strs = norm_strs

        if not f_items:
            return []

        # Pass 1 — fast WRatio scan to get top candidates (2× top_k)
        fast_matches = process.extract(
            query_n,
            f_norm_strs,
            scorer=fuzz.WRatio,
            limit=top_k * 2,
        )

        # Pass 2 — full 3-way blend re-score + boosting
        results = []
        seen    = set()

        for _text, _fast_score, index in fast_matches:
            if index in seen:
                continue
            seen.add(index)

            base_score = blend_score(query_n, f_norm_strs[index], f_raw_strs[index])
            if base_score < self.min_score:
                continue

            # Apply boosting based on product name
            product_name_n = normalize(f_items[index].get("name", ""))
            final_score    = apply_boost(base_score, query_n, product_name_n)

            result = dict(f_items[index])
            result["score"]       = round(final_score, 2)
            result["score_pct"]   = round(final_score, 2)
            result["score_label"] = self._label(final_score)
            results.append(result)

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]

    def search_with_field_scores(
        self,
        query: str,
        top_k: int = SEARCH_DEFAULT_K,
        filters: Optional[Dict] = None,
    ) -> List[Dict[str, Any]]:
        """
        Same as search() but also returns per-field scores.
        Useful for debugging and bucketing.
        """
        results  = self.search(query, top_k, filters=filters)
        query_n  = normalize(apply_synonyms(query))

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


# ── Module-level singleton ─────────────────────────────────────────────────────
_engine: Optional[FuzzySearchEngine] = None
_engine_lock = threading.Lock()


def get_engine(rebuild_interval: Optional[int] = None) -> FuzzySearchEngine:
    """
    Return the module-level singleton engine, creating it on first call.

    The rebuild_interval is only honoured on the FIRST call (when the engine
    is created). Subsequent calls return the existing instance unchanged.
    This prevents duplicate background threads when the module is imported
    multiple times (e.g. Flask debug reloader).
    """
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                _engine = FuzzySearchEngine(rebuild_interval=rebuild_interval)
    return _engine
