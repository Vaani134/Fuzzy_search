"""
modules/synonym_suggester.py
----------------------------
Intelligent synonym suggestion engine.

How it works
------------
1. Pull "weak" queries from search_history:
   - top_score < 70  (returned results but with low fuzzy confidence)
   - OR is_zero_result = 1  (no results at all)
   - OR result_count < LOW_RESULT_THRESHOLD  (very few results, fallback)
   Queries that already exist in synonyms or synonym_suggestions are skipped.

   Why top_score is the primary signal
   ------------------------------------
   Fuzzy search returns results for almost any query — "grdiner" returns
   grinder products at score ~60.  result_count alone cannot distinguish
   a correct query ("grinder", score 95) from a typo ("grdiner", score 60).
   top_score captures match confidence, not just result existence.

2. Extract a keyword vocabulary from the product catalog:
   - Tokenise product names (lowercase, strip symbols, drop stopwords)
   - Deduplicate; keep the top KEYWORD_POOL_LIMIT most-frequent tokens
   This vocabulary adapts automatically when the product DB changes.

3. For each weak query, find the best-matching keyword using RapidFuzz WRatio.
   Queries are first validated by is_valid_query() to reject garbage input.
   Matching is done token-by-token (not full-string) for better accuracy.
   A candidate is accepted when:
     SCORE_MIN (70) ≤ score ≤ SCORE_MAX (88)
   - Below 70: too distant or gibberish — raised from 60 to eliminate noise
   - Above 88: already a strong match — the engine handles it fine without
     a synonym; adding one would be redundant

4. Store accepted candidates in synonym_suggestions with status='pending'.
   INSERT OR IGNORE so re-running never overwrites existing rows.

5. Admin reviews via the API:
   POST /api/synonyms/approve/<id>  → copies to synonyms table, reloads memory
   POST /api/synonyms/reject/<id>   → marks status='rejected'

Safety rules enforced before every insert
------------------------------------------
- variant must differ from canonical
- variant must not already exist in synonyms (active mapping)
- variant must not already exist in synonym_suggestions (pending/rejected)
- No circular mapping: if canonical → X already exists, variant → canonical
  would create a loop (variant → canonical → X)

Performance
-----------
- Keyword pool capped at KEYWORD_POOL_LIMIT (default 5000 unique tokens)
- Weak query set capped at MAX_QUERIES_PER_RUN (default 200)
- Suggestions per run capped at MAX_SUGGESTIONS_PER_RUN (default 50)
- process.extractOne() is used (single best match per query) — O(n) per query
  against the keyword pool, but n ≤ 5000 so each call is < 5 ms
- The suggester never touches search() — zero impact on search latency
"""

import re
import sys
import os
from datetime import datetime, timezone
from typing import List, Dict, Set, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db.database import get_connection

try:
    from rapidfuzz import fuzz, process as rf_process
    _RAPIDFUZZ_OK = True
except ImportError:
    _RAPIDFUZZ_OK = False


# ── Tunable constants ──────────────────────────────────────────────────────────

# Score band for accepting a candidate (WRatio, 0–100)
# Raised from 60 to 70 to eliminate weak/meaningless matches.
# Real typos ("grdiner"→"grinder") score 85+, well above this floor.
# Gibberish ("xyzxyz", "gke") scores < 45, safely below it.
SCORE_MIN: float = 70.0   # below this → too distant or gibberish
SCORE_MAX: float = 88.0   # above this → already works fine without a synonym

# Queries with result_count below this are treated as "weak"
LOW_RESULT_THRESHOLD: int = 3

# Maximum number of unique keyword tokens extracted from product names
KEYWORD_POOL_LIMIT: int = 5_000

# Maximum weak queries processed per run
MAX_QUERIES_PER_RUN: int = 200

# Maximum new suggestions stored per run
MAX_SUGGESTIONS_PER_RUN: int = 50

# English stopwords — tokens that carry no product-domain meaning
_STOPWORDS: Set[str] = {
    "a", "an", "the", "and", "or", "of", "in", "on", "at", "to", "for",
    "with", "by", "from", "as", "is", "it", "its", "be", "are", "was",
    "were", "has", "have", "had", "do", "does", "did", "not", "no",
    "this", "that", "these", "those", "each", "per", "set", "lot",
    "box", "bag", "pack", "unit", "case", "piece", "pcs", "pc",
    "new", "old", "big", "small", "large", "mini", "size",
    "color", "colour", "black", "white", "red", "blue", "green",
    "x", "oz", "ml", "mg", "kg", "lb", "ct", "pk",
}


# ── Text helpers ───────────────────────────────────────────────────────────────

def _tokenize(text: str) -> List[str]:
    """
    Lowercase, strip non-alphanumeric characters, split on whitespace,
    and remove stopwords and very short tokens (len < 3).
    """
    if not text or not isinstance(text, str):
        return []
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return [
        t for t in text.split()
        if len(t) >= 3 and t not in _STOPWORDS
    ]


# ── Query validation ──────────────────────────────────────────────────────────

def is_valid_query(query: str) -> bool:
    """
    Return True only if *query* looks like a genuine user search term that
    could plausibly be a misspelling of a real product keyword.

    Validation rules (all must pass)
    ---------------------------------
    1. Total length ≥ 3 characters after stripping whitespace.
       Catches empty strings and single-character noise.

    2. No more than 3 whitespace-separated tokens.
       Genuine product typos are short ("grdiner", "hookha").
       Long phrases ("vmegoqnr qoibsqssvbot emcskaseuwqoew rwf") are garbage.

    3. At least one meaningful token (length ≥ 4, not a stopword).
       Catches queries like "gke" (all tokens too short — len 3 < 4) or
       pure stopword sequences ("a of the").

    4. Multi-word queries (> 2 tokens) require at least one token that is
       ≥ 4 characters long.
       Allows "glass pip" (tokens: "glass", "pip") but blocks short-token
       noise like "ab cd ef".

    Why this function exists
    ------------------------
    The fuzzy engine will always find *some* match in the keyword pool, even
    for random strings.  Without pre-filtering, garbage queries like
    "vmegoqnr qoibsqssvbot" can score above SCORE_MIN by accident and
    pollute the suggestion queue.  Validating before fuzzy matching is
    cheaper and more reliable than relying on score thresholds alone.

    Parameters
    ----------
    query : str
        Raw query string from search_history (already lowercased by analytics).

    Returns
    -------
    bool — True if the query is worth fuzzy-matching, False if it should
           be skipped entirely.
    """
    q = query.strip()

    # Rule 1: minimum total length
    if len(q) < 3:
        return False

    # Split into raw tokens (before stopword filtering) to count words
    raw_tokens = q.split()

    # Rule 2: reject long multi-word phrases — real typos are short
    if len(raw_tokens) > 3:
        return False

    # Rule 3: at least one meaningful token (len ≥ 4, not a stopword).
    # Minimum 4 characters because 3-char tokens like "gke" are too short
    # to be reliable product keyword typos.  Real product terms are at least
    # 4 characters: "pipe", "vape", "bong", "hookah", "grinder", etc.
    meaningful = [t for t in raw_tokens if len(t) >= 4 and t not in _STOPWORDS]
    if not meaningful:
        return False

    # Rule 4: multi-word queries need at least one substantive token (len ≥ 4)
    # "glass pip" passes (glass=5 ≥ 4 — the substantive token)
    # "ab cd ef" fails (no token ≥ 4)
    if len(raw_tokens) > 2:
        if not any(len(t) >= 4 for t in raw_tokens if t not in _STOPWORDS):
            return False

    return True


def _best_token_match(
    query: str,
    keyword_pool: List[str],
) -> Optional[tuple]:
    """
    Split *query* into tokens and return the best RapidFuzz match across
    all tokens, rather than matching the full query string.

    Why token-level matching instead of full-string matching
    --------------------------------------------------------
    Full-string matching penalises multi-word queries.  "glass pip" scored
    90 against "glass" (above SCORE_MAX) but the real typo is "pip" → "pipe".
    Matching each token separately finds the intended correction:
        "pip"   → "pipe"   score ~85  ✓  (within band)
        "glass" → "glass"  score 100  ✗  (above SCORE_MAX, identity)

    For single-token queries the result is identical to full-string matching.

    Parameters
    ----------
    query        : the weak query string (already validated by is_valid_query)
    keyword_pool : list of canonical keyword strings to match against

    Returns
    -------
    (matched_keyword: str, score: float, token: str) or None if no tokens
    produce a match above 0.
    """
    if not _RAPIDFUZZ_OK or not keyword_pool:
        return None

    tokens = _tokenize(query)   # already strips stopwords and short tokens
    if not tokens:
        return None

    best_keyword: Optional[str] = None
    best_score:   float         = 0.0
    best_token:   str           = ""

    for token in tokens:
        result = rf_process.extractOne(token, keyword_pool, scorer=fuzz.WRatio)
        if result is None:
            continue
        matched, score, _ = result
        # Only update if this token produces a strictly better match
        if score > best_score:
            best_score   = score
            best_keyword = matched
            best_token   = token

    if best_keyword is None:
        return None

    return (best_keyword, best_score, best_token)


# ── Core functions ─────────────────────────────────────────────────────────────

def build_keyword_pool(limit: int = KEYWORD_POOL_LIMIT) -> List[str]:
    """
    Extract a deduplicated keyword vocabulary from active product names.

    Strategy
    --------
    - Tokenise every active product name
    - Count token frequency
    - Return the top `limit` tokens by frequency (most common product words
      are the most useful correction targets)

    This function re-reads the DB every time it is called so it automatically
    adapts when the product catalog changes (e.g. after a MySQL sync).

    Parameters
    ----------
    limit : int
        Maximum number of unique tokens to return.

    Returns
    -------
    list of str — unique keyword tokens, most frequent first.
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT name FROM products WHERE is_inactive = 0 LIMIT 50000"
        ).fetchall()
    finally:
        conn.close()

    freq: Dict[str, int] = {}
    for row in rows:
        for token in _tokenize(row["name"]):
            freq[token] = freq.get(token, 0) + 1

    # Sort by frequency descending, return top `limit` tokens
    sorted_tokens = sorted(freq, key=lambda t: freq[t], reverse=True)
    return sorted_tokens[:limit]


def _load_weak_queries(
    max_queries: int = MAX_QUERIES_PER_RUN,
    low_result_threshold: int = LOW_RESULT_THRESHOLD,
    weak_score_threshold: float = 70.0,
) -> List[str]:
    """
    Return queries from search_history that produced weak results and are
    not already covered by an existing synonym or pending suggestion.

    A query is "weak" if ANY of the following is true:
      - top_score < weak_score_threshold (70)  — returned results but with
        low fuzzy confidence; the engine is guessing.  This is the primary
        signal: "grdiner" returns grinder results at score ~60, which is
        below 70, so it's correctly flagged as weak.
      - is_zero_result = 1                     — no results at all
      - result_count < low_result_threshold    — very few results (fallback)

    Why top_score is better than result_count alone
    ------------------------------------------------
    Fuzzy search returns results for almost any query — even "grdiner" returns
    grinder products.  result_count alone cannot distinguish between a correct
    query ("grinder", score 95) and a typo ("grdiner", score 60).  top_score
    captures the confidence of the match, not just whether results exist.

    Queries already in synonyms.variant or synonym_suggestions.variant
    (any status) are excluded — no point re-suggesting them.

    Parameters
    ----------
    max_queries           : hard cap on rows returned
    low_result_threshold  : result_count below this is also treated as weak
    weak_score_threshold  : top_score below this is treated as weak (default 70)
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT sh.query
            FROM search_history sh
            WHERE (
                sh.top_score < ?
                OR sh.is_zero_result = 1
                OR sh.result_count < ?
            )
              AND sh.query NOT IN (SELECT variant FROM synonyms)
              AND sh.query NOT IN (SELECT variant FROM synonym_suggestions)
            ORDER BY sh.search_count DESC
            LIMIT ?
            """,
            (weak_score_threshold, low_result_threshold, max_queries),
        ).fetchall()
        return [r["query"] for r in rows]
    finally:
        conn.close()


def _load_existing_variants() -> Set[str]:
    """Return all variants already in synonyms or synonym_suggestions."""
    conn = get_connection()
    try:
        syn  = {r["variant"] for r in conn.execute("SELECT variant FROM synonyms").fetchall()}
        sugg = {r["variant"] for r in conn.execute("SELECT variant FROM synonym_suggestions").fetchall()}
        return syn | sugg
    finally:
        conn.close()


def _load_existing_canonicals() -> Dict[str, str]:
    """
    Return a dict of variant → canonical for all active synonyms.
    Used to detect circular mappings before inserting a suggestion.
    """
    conn = get_connection()
    try:
        rows = conn.execute("SELECT variant, canonical FROM synonyms").fetchall()
        return {r["variant"]: r["canonical"] for r in rows}
    finally:
        conn.close()


def _is_circular(variant: str, canonical: str, canonicals: Dict[str, str]) -> bool:
    """
    Return True if adding variant → canonical would create a circular chain.

    Example of a circular mapping:
        Existing:  hookah → shisha
        Proposed:  shisha → hookah   ← circular (A→B and B→A)

    We walk the canonical chain up to 10 hops to catch longer cycles.
    """
    seen = {variant}
    current = canonical
    for _ in range(10):
        if current in seen:
            return True
        seen.add(current)
        current = canonicals.get(current)
        if current is None:
            break
    return False


def generate_suggestions(
    keyword_pool: Optional[List[str]] = None,
    max_suggestions: int = MAX_SUGGESTIONS_PER_RUN,
) -> List[Dict]:
    """
    Main entry point.  Generates synonym candidates and stores them in
    synonym_suggestions with status='pending'.

    Parameters
    ----------
    keyword_pool : list of str, optional
        Pre-built keyword vocabulary.  If None, build_keyword_pool() is called.
    max_suggestions : int
        Hard cap on new rows inserted per run.

    Returns
    -------
    list of dicts — the newly inserted suggestions:
        {variant, canonical, score}
    """
    if not _RAPIDFUZZ_OK:
        print("[SynonymSuggester] rapidfuzz not available — skipping.")
        return []

    # ── Build inputs ──────────────────────────────────────────────────────────
    if keyword_pool is None:
        keyword_pool = build_keyword_pool()

    if not keyword_pool:
        print("[SynonymSuggester] No keywords extracted from products.")
        return []

    weak_queries    = _load_weak_queries()
    existing_vars   = _load_existing_variants()
    existing_cans   = _load_existing_canonicals()

    if not weak_queries:
        print("[SynonymSuggester] No weak queries found — nothing to suggest.")
        return []

    print(
        f"[SynonymSuggester] Analysing {len(weak_queries)} weak queries "
        f"against {len(keyword_pool)} keywords…"
    )

    # ── Score each weak query against the keyword pool ────────────────────────
    new_suggestions: List[Dict] = []
    now = datetime.now(timezone.utc).isoformat()

    conn = get_connection()
    try:
        for query in weak_queries:
            if len(new_suggestions) >= max_suggestions:
                break

            # Skip if already covered (race condition guard)
            if query in existing_vars:
                continue

            # ── Gate 1: query validation ──────────────────────────────────────
            # Reject garbage before any fuzzy work.  This filters out:
            #   - random character strings ("vmegoqnr qoibsqssvbot…")
            #   - overly long phrases (> 3 tokens)
            #   - queries with no meaningful tokens (all too short / stopwords)
            # Cheap string checks here save expensive fuzzy calls below.
            if not is_valid_query(query):
                continue

            # ── Gate 2: token-level fuzzy matching ────────────────────────────
            # Match each token of the query separately and take the best score.
            # This is more accurate than full-string matching for multi-word
            # queries: "glass pip" → token "pip" → "pipe" (score ~85)
            # rather than full string "glass pip" → "glass" (score 90, above max).
            match_result = _best_token_match(query, keyword_pool)
            if match_result is None:
                continue

            matched_keyword, score, matched_token = match_result

            # ── Gate 3: score band filter ─────────────────────────────────────
            # SCORE_MIN raised to 70 (was 60) to eliminate weak/noisy matches.
            # SCORE_MAX stays at 88 — above this the engine already handles
            # the query well without a synonym.
            if score < SCORE_MIN or score > SCORE_MAX:
                continue

            # ── Gate 4: safety checks ─────────────────────────────────────────
            # The canonical stored is the matched keyword, not the matched token,
            # so the synonym maps the full query to the correct product term.
            # e.g. query="glass pip", token="pip", canonical="pipe"
            #      → synonym: "glass pip" → "pipe"  (correct)

            # variant must differ from canonical
            if query == matched_keyword:
                continue

            # no circular mapping
            if _is_circular(query, matched_keyword, existing_cans):
                continue

            # INSERT OR IGNORE — never overwrites an existing suggestion
            try:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO synonym_suggestions
                        (variant, canonical, score, status, created_at)
                    VALUES (?, ?, ?, 'pending', ?)
                    """,
                    (query, matched_keyword, round(score, 2), now),
                )
                conn.commit()

                # Track for return value (only count rows actually inserted)
                inserted = conn.execute(
                    "SELECT id FROM synonym_suggestions WHERE variant = ? AND created_at = ?",
                    (query, now),
                ).fetchone()
                if inserted:
                    new_suggestions.append({
                        "variant":   query,
                        "canonical": matched_keyword,
                        "score":     round(score, 2),
                    })
                    existing_vars.add(query)   # prevent duplicate within this run

            except Exception as exc:
                print(f"[SynonymSuggester] Insert failed for '{query}': {exc}")

    finally:
        conn.close()

    print(f"[SynonymSuggester] Generated {len(new_suggestions)} new suggestion(s).")
    return new_suggestions


def get_pending_suggestions(limit: int = 100) -> List[Dict]:
    """Return all pending synonym suggestions, highest score first."""
    limit = min(max(1, limit), 500)
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT id, variant, canonical, score, status, created_at
            FROM synonym_suggestions
            WHERE status = 'pending'
            ORDER BY score DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_all_suggestions(limit: int = 200) -> List[Dict]:
    """Return all suggestions (any status), newest first."""
    limit = min(max(1, limit), 500)
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT id, variant, canonical, score, status, created_at
            FROM synonym_suggestions
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def approve_suggestion(suggestion_id: int) -> Dict:
    """
    Approve a pending suggestion:
      1. Fetch the suggestion row
      2. Validate it is still safe to add (no duplicate, no circular)
      3. INSERT OR IGNORE into synonyms
      4. Mark suggestion as 'approved'
      5. Return result dict

    Does NOT call reload_synonyms() — the route layer does that so the
    HTTP response includes the updated synonym count.
    """
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM synonym_suggestions WHERE id = ?",
            (suggestion_id,),
        ).fetchone()

        if not row:
            return {"error": f"Suggestion id={suggestion_id} not found.", "code": 404}

        if row["status"] != "pending":
            return {
                "error": f"Suggestion is already '{row['status']}' — cannot approve.",
                "code": 409,
            }

        variant   = row["variant"]
        canonical = row["canonical"]

        # Guard: variant must not already be in synonyms
        existing = conn.execute(
            "SELECT id FROM synonyms WHERE variant = ?", (variant,)
        ).fetchone()
        if existing:
            # Mark approved anyway (synonym already exists — consistent state)
            conn.execute(
                "UPDATE synonym_suggestions SET status = 'approved' WHERE id = ?",
                (suggestion_id,),
            )
            conn.commit()
            return {
                "status":  "already_exists",
                "message": f"'{variant}' already maps to a synonym. Marked approved.",
                "variant": variant, "canonical": canonical,
            }

        # Guard: no circular mapping
        canonicals = {
            r["variant"]: r["canonical"]
            for r in conn.execute("SELECT variant, canonical FROM synonyms").fetchall()
        }
        if _is_circular(variant, canonical, canonicals):
            conn.execute(
                "UPDATE synonym_suggestions SET status = 'rejected' WHERE id = ?",
                (suggestion_id,),
            )
            conn.commit()
            return {
                "error":  f"Circular mapping detected: '{variant}' → '{canonical}'. Auto-rejected.",
                "code":   409,
            }

        # Insert into synonyms
        conn.execute(
            "INSERT OR IGNORE INTO synonyms (variant, canonical, created_at) VALUES (?, ?, ?)",
            (variant, canonical, datetime.now(timezone.utc).isoformat()),
        )
        # Mark suggestion approved
        conn.execute(
            "UPDATE synonym_suggestions SET status = 'approved' WHERE id = ?",
            (suggestion_id,),
        )
        conn.commit()

    finally:
        conn.close()

    return {
        "status":    "ok",
        "variant":   variant,
        "canonical": canonical,
    }


def reject_suggestion(suggestion_id: int) -> Dict:
    """Mark a pending suggestion as 'rejected'."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id, variant, status FROM synonym_suggestions WHERE id = ?",
            (suggestion_id,),
        ).fetchone()

        if not row:
            return {"error": f"Suggestion id={suggestion_id} not found.", "code": 404}

        if row["status"] != "pending":
            return {
                "error": f"Suggestion is already '{row['status']}' — cannot reject.",
                "code": 409,
            }

        conn.execute(
            "UPDATE synonym_suggestions SET status = 'rejected' WHERE id = ?",
            (suggestion_id,),
        )
        conn.commit()

    finally:
        conn.close()

    return {"status": "ok", "rejected_id": suggestion_id, "variant": row["variant"]}
