"""
routes/search_routes.py
-----------------------
All search-related API endpoints, extracted from app.py for clean separation.

Endpoints
---------
  GET  /api/search              — paginated, filtered, sorted fuzzy search
  GET  /api/search/history      — recent search queries (analytics)
  GET  /api/search/top          — most-frequent queries (analytics)
  POST /api/search/rebuild      — rebuild in-memory index
  GET  /api/autocomplete        — fast autocomplete suggestions
  GET  /api/cache/stats         — cache statistics
  POST /api/cache/clear         — clear the search cache
"""

import math
from flask import Blueprint, request, jsonify

from modules.fuzzy_search import get_engine, apply_synonyms, get_query_suggestion
from modules.autocomplete import get_suggestions
from modules.analytics import log_search, get_recent_searches, get_top_queries, \
    get_zero_result_queries, get_trending_queries
from modules.cache import search_cache
from config import SEARCH_DEFAULT_K

search_bp = Blueprint("search", __name__)


# ── Helper: parse numeric query param safely ──────────────────────────────────

def _float_param(name: str, default=None):
    """Parse a float query parameter, returning default on failure."""
    val = request.args.get(name, "").strip()
    if not val:
        return default
    try:
        return float(val)
    except ValueError:
        return default


def _int_param(name: str, default: int = 0) -> int:
    """Parse an int query parameter, returning default on failure."""
    val = request.args.get(name, "").strip()
    if not val:
        return default
    try:
        return int(val)
    except ValueError:
        return default


# ── GET /api/search ────────────────────────────────────────────────────────────

@search_bp.route("/api/search")
def api_search():
    """
    Paginated, filtered, sorted fuzzy product search.

    Query parameters
    ----------------
    q          : str   — search query (required)
    page       : int   — page number, 1-based (default: 1)
    limit      : int   — results per page (default: 20, max: 100)
    sort       : str   — "score" (default) | "name"
    category   : str   — filter by category name (partial, case-insensitive)
    min_price  : float — minimum price filter
    max_price  : float — maximum price filter

    Response
    --------
    {
        "query":         "...",
        "expanded_query": "...",   // after synonym expansion
        "page":          1,
        "limit":         20,
        "total_results": 42,
        "total_pages":   3,
        "sort":          "score",
        "filters":       {...},
        "results":       [...]
    }
    """
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"error": "q parameter is required"}), 400

    # Pagination
    page  = max(1, _int_param("page", 1))
    limit = min(max(1, _int_param("limit", SEARCH_DEFAULT_K)), 100)

    # Sorting
    sort = request.args.get("sort", "score").strip().lower()
    if sort not in ("score", "name"):
        sort = "score"

    # Filters
    filters = {
        "category":  request.args.get("category", "").strip(),
        "min_price": _float_param("min_price"),
        "max_price": _float_param("max_price"),
    }
    # Remove empty filter values for cleaner cache keys
    active_filters = {k: v for k, v in filters.items() if v not in (None, "")}

    # ── Cache lookup ──────────────────────────────────────────────────────────
    cache_key = search_cache.make_key(query, active_filters, page, limit, sort)
    cached    = search_cache.get(cache_key)
    if cached is not None:
        return jsonify(cached)

    # ── Execute search ────────────────────────────────────────────────────────
    engine          = get_engine()
    expanded_query  = apply_synonyms(query)

    # Fetch all matching results (up to hard cap) then paginate in Python.
    # This is correct because fuzzy scoring must see all candidates before
    # we can reliably paginate by score rank.
    all_results = engine.search_with_field_scores(
        query,
        top_k=100,
        filters=active_filters if active_filters else None,
    )

    # ── Sorting ───────────────────────────────────────────────────────────────
    if sort == "name":
        all_results.sort(key=lambda r: (r.get("name") or "").lower())
    # "score" is already sorted descending by the engine

    # ── Pagination ────────────────────────────────────────────────────────────
    total_results = len(all_results)
    total_pages   = max(1, math.ceil(total_results / limit))
    page          = min(page, total_pages)
    start         = (page - 1) * limit
    end           = start + limit
    page_results  = all_results[start:end]

    # ── "Did You Mean" suggestion ─────────────────────────────────────────────
    # Build a choices pool from the top result names (real product vocabulary)
    # plus the static keyword list inside get_query_suggestion().
    # We cap at 200 names so this stays fast — the top results are the most
    # relevant candidates anyway.
    top_names = [r["name"] for r in all_results[:200] if r.get("name")]
    # top_score: score of the best result — used by suggestion logic AND
    # persisted to search_history so the synonym suggester can detect
    # queries that returned results but with low confidence (score < 70).
    top_score = all_results[0]["score"] if all_results else 0.0
    suggestion = get_query_suggestion(
        query,
        choices=top_names,
        top_result_score=top_score,
    )

    # ── Log to analytics ──────────────────────────────────────────────────────
    # top_score is stored so the synonym suggester can distinguish between
    # "grdiner" (returns results but score ~60 → weak) and
    # "grinder"  (returns results with score ~95 → strong, skip).
    log_search(query, total_results, top_score)

    # ── Build response ────────────────────────────────────────────────────────
    response = {
        "query":          query,
        "expanded_query": expanded_query if expanded_query != query else query,
        "page":           page,
        "limit":          limit,
        "total_results":  total_results,
        "total_pages":    total_pages,
        "sort":           sort,
        "filters":        active_filters,
        "results":        page_results,
        # suggestion is None when the query is already a strong match (score ≥ 70)
        # or too distant from anything known (score < 35).
        "suggestion":     suggestion,
    }

    # Cache the response
    search_cache.set(cache_key, response)

    return jsonify(response)


# ── GET /api/search/history ────────────────────────────────────────────────────

@search_bp.route("/api/search/history")
def api_search_history():
    """
    GET /api/search/history?limit=10
    Returns the most recently searched queries (by last_searched desc).
    Each row includes search_count, is_zero_result, and last_searched.
    """
    limit = min(max(1, _int_param("limit", 10)), 100)
    return jsonify(get_recent_searches(limit=limit))


# ── GET /api/search/top ────────────────────────────────────────────────────────

@search_bp.route("/api/search/top")
def api_search_top():
    """
    GET /api/search/top?limit=10
    Returns the most frequently searched queries (by cumulative search_count).
    """
    limit = min(max(1, _int_param("limit", 10)), 100)
    return jsonify(get_top_queries(limit=limit))


# ── GET /api/search/zero-results ──────────────────────────────────────────────

@search_bp.route("/api/search/zero-results")
def api_search_zero_results():
    """
    GET /api/search/zero-results?limit=10
    Returns queries that currently return zero results, ranked by how
    often they have been attempted.  Useful for identifying catalog gaps.
    """
    limit = min(max(1, _int_param("limit", 10)), 100)
    return jsonify(get_zero_result_queries(limit=limit))


# ── GET /api/search/trending ───────────────────────────────────────────────────

@search_bp.route("/api/search/trending")
def api_search_trending():
    """
    GET /api/search/trending?hours=24&limit=10
    Returns the most-searched queries within the last N hours.
    Default window: 24 hours.
    """
    hours = min(max(1, _int_param("hours", 24)), 720)   # cap at 30 days
    limit = min(max(1, _int_param("limit", 10)), 100)
    return jsonify(get_trending_queries(hours=hours, limit=limit))


# ── POST /api/search/rebuild ───────────────────────────────────────────────────

@search_bp.route("/api/search/rebuild", methods=["POST"])
def api_rebuild():
    """
    POST /api/search/rebuild
    Rebuild the in-memory search index from SQLite.
    Also clears the search cache so stale results are not served.
    """
    engine = get_engine()
    count  = engine.rebuild()
    cleared = search_cache.clear()
    return jsonify({
        "status":        "ok",
        "indexed":       count,
        "cache_cleared": cleared,
    })


# ── GET /api/autocomplete ──────────────────────────────────────────────────────

@search_bp.route("/api/autocomplete")
def api_autocomplete():
    """
    GET /api/autocomplete?q=gla&limit=10
    Returns fast autocomplete suggestions (prefix + contains match).
    """
    query = request.args.get("q", "").strip()
    limit = min(max(1, _int_param("limit", 10)), 20)

    if not query or len(query) < 2:
        return jsonify([])

    suggestions = get_suggestions(query, limit=limit)
    return jsonify(suggestions)


# ── GET /api/cache/stats ───────────────────────────────────────────────────────

@search_bp.route("/api/cache/stats")
def api_cache_stats():
    """GET /api/cache/stats — current cache statistics."""
    return jsonify(search_cache.stats())


# ── POST /api/cache/clear ──────────────────────────────────────────────────────

@search_bp.route("/api/cache/clear", methods=["POST"])
def api_cache_clear():
    """POST /api/cache/clear — flush the entire search cache."""
    cleared = search_cache.clear()
    return jsonify({"status": "ok", "cleared": cleared})
