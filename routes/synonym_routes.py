"""
routes/synonym_routes.py
------------------------
CRUD API for the synonyms table + admin approval flow for suggestions.

Endpoints
---------
  GET    /api/synonyms                    — list all active synonyms
  POST   /api/synonyms/add               — add a new synonym pair manually
  DELETE /api/synonyms/<id>              — delete an active synonym

  POST   /api/synonyms/suggest           — run the suggester (generate candidates)
  GET    /api/synonyms/suggestions       — list pending suggestions
  GET    /api/synonyms/suggestions/all   — list all suggestions (any status)
  POST   /api/synonyms/approve/<id>      — approve → move to synonyms table
  POST   /api/synonyms/reject/<id>       — reject a suggestion

After every mutation that changes the active synonyms table, reload_synonyms()
is called so the change takes effect immediately in apply_synonyms() — no
server restart needed.  The search cache is also cleared.
"""

from datetime import datetime, timezone
from flask import Blueprint, request, jsonify

from db.database import get_connection
from modules.fuzzy_search import reload_synonyms
from modules.cache import search_cache
from modules.synonym_suggester import (
    generate_suggestions,
    get_pending_suggestions,
    get_all_suggestions,
    approve_suggestion,
    reject_suggestion,
)

synonym_bp = Blueprint("synonyms", __name__)


# ── GET /api/synonyms ──────────────────────────────────────────────────────────

@synonym_bp.route("/api/synonyms", methods=["GET"])
def api_list_synonyms():
    """
    GET /api/synonyms
    Returns all synonym pairs ordered alphabetically by variant.

    Response
    --------
    [
      { "id": 1, "variant": "hooka", "canonical": "hookah", "created_at": "..." },
      ...
    ]
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, variant, canonical, created_at "
            "FROM synonyms ORDER BY variant ASC"
        ).fetchall()
        return jsonify([dict(r) for r in rows])
    finally:
        conn.close()


# ── POST /api/synonyms/add ─────────────────────────────────────────────────────

@synonym_bp.route("/api/synonyms/add", methods=["POST"])
def api_add_synonym():
    """
    POST /api/synonyms/add
    Body (JSON): { "variant": "hooka", "canonical": "hookah" }

    Rules
    -----
    • Both fields are required and must be non-empty strings.
    • variant is stored lowercase and stripped.
    • canonical is stored lowercase and stripped.
    • variant must be unique — returns 409 if it already exists.
    • After insert, reload_synonyms() is called so the change is live
      immediately without a server restart.
    • The search cache is cleared so stale results are not served.

    Response (201)
    --------------
    { "status": "ok", "id": 42, "variant": "hooka", "canonical": "hookah",
      "synonyms_loaded": 25 }
    """
    body = request.get_json(silent=True) or {}

    variant   = str(body.get("variant",   "") or "").strip().lower()
    canonical = str(body.get("canonical", "") or "").strip().lower()

    # ── Validation ────────────────────────────────────────────────────────────
    if not variant:
        return jsonify({"error": "'variant' is required and must be non-empty."}), 400
    if not canonical:
        return jsonify({"error": "'canonical' is required and must be non-empty."}), 400
    if variant == canonical:
        return jsonify({"error": "'variant' and 'canonical' must be different."}), 400

    conn = get_connection()
    try:
        # Check for duplicate variant
        existing = conn.execute(
            "SELECT id, canonical FROM synonyms WHERE variant = ?", (variant,)
        ).fetchone()
        if existing:
            return jsonify({
                "error": f"Variant '{variant}' already maps to '{existing['canonical']}'. "
                         f"Delete id={existing['id']} first if you want to remap it.",
                "existing_id": existing["id"],
            }), 409

        # Insert
        cursor = conn.execute(
            "INSERT INTO synonyms (variant, canonical, created_at) VALUES (?, ?, ?)",
            (variant, canonical, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        new_id = cursor.lastrowid

    finally:
        conn.close()

    # ── Hot-reload synonyms + clear cache ─────────────────────────────────────
    count = reload_synonyms()
    search_cache.clear()

    return jsonify({
        "status":          "ok",
        "id":              new_id,
        "variant":         variant,
        "canonical":       canonical,
        "synonyms_loaded": count,
    }), 201


# ── DELETE /api/synonyms/<id> ──────────────────────────────────────────────────

@synonym_bp.route("/api/synonyms/<int:synonym_id>", methods=["DELETE"])
def api_delete_synonym(synonym_id: int):
    """
    DELETE /api/synonyms/<id>
    Removes the synonym with the given id.

    Returns 404 if the id does not exist.
    After deletion, reload_synonyms() is called and the cache is cleared.

    Response (200)
    --------------
    { "status": "ok", "deleted_id": 42, "synonyms_loaded": 24 }
    """
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id, variant, canonical FROM synonyms WHERE id = ?",
            (synonym_id,),
        ).fetchone()

        if not row:
            return jsonify({"error": f"Synonym id={synonym_id} not found."}), 404

        conn.execute("DELETE FROM synonyms WHERE id = ?", (synonym_id,))
        conn.commit()
        deleted = dict(row)

    finally:
        conn.close()

    # ── Hot-reload synonyms + clear cache ─────────────────────────────────────
    count = reload_synonyms()
    search_cache.clear()

    return jsonify({
        "status":          "ok",
        "deleted_id":      synonym_id,
        "deleted_variant": deleted["variant"],
        "synonyms_loaded": count,
    })


# ── POST /api/synonyms/suggest ─────────────────────────────────────────────────

@synonym_bp.route("/api/synonyms/suggest", methods=["POST"])
def api_run_suggester():
    """
    POST /api/synonyms/suggest
    Body (JSON, optional): { "max_suggestions": 50 }

    Runs the synonym suggester:
      1. Extracts keywords from the current product catalog
      2. Finds weak queries in search_history (zero / low results)
      3. Matches them against keywords using RapidFuzz (score 60–85)
      4. Stores candidates in synonym_suggestions with status='pending'

    This is a synchronous call — it may take a few seconds on large catalogs.
    For 40k products and 200 queries it typically completes in < 2 seconds.

    Response (200)
    --------------
    {
      "status": "ok",
      "new_suggestions": 7,
      "suggestions": [
        { "variant": "grdiner", "canonical": "grinder", "score": 85.7 },
        ...
      ]
    }
    """
    body = request.get_json(silent=True) or {}
    max_s = min(max(1, int(body.get("max_suggestions", 50))), 200)

    suggestions = generate_suggestions(max_suggestions=max_s)

    return jsonify({
        "status":          "ok",
        "new_suggestions": len(suggestions),
        "suggestions":     suggestions,
    })


# ── GET /api/synonyms/suggestions ─────────────────────────────────────────────

@synonym_bp.route("/api/synonyms/suggestions", methods=["GET"])
def api_list_suggestions():
    """
    GET /api/synonyms/suggestions?limit=100
    Returns pending synonym suggestions, highest score first.

    Response
    --------
    [
      { "id": 1, "variant": "grdiner", "canonical": "grinder",
        "score": 85.7, "status": "pending", "created_at": "..." },
      ...
    ]
    """
    limit = min(max(1, int(request.args.get("limit", 100))), 500)
    return jsonify(get_pending_suggestions(limit=limit))


# ── GET /api/synonyms/suggestions/all ─────────────────────────────────────────

@synonym_bp.route("/api/synonyms/suggestions/all", methods=["GET"])
def api_list_all_suggestions():
    """
    GET /api/synonyms/suggestions/all?limit=200
    Returns all suggestions regardless of status, newest first.
    Useful for auditing what the suggester has produced over time.
    """
    limit = min(max(1, int(request.args.get("limit", 200))), 500)
    return jsonify(get_all_suggestions(limit=limit))


# ── POST /api/synonyms/approve/<id> ───────────────────────────────────────────

@synonym_bp.route("/api/synonyms/approve/<int:suggestion_id>", methods=["POST"])
def api_approve_suggestion(suggestion_id: int):
    """
    POST /api/synonyms/approve/<id>

    Approves a pending suggestion:
      1. Validates no duplicate / circular mapping
      2. Inserts into synonyms table
      3. Marks suggestion as 'approved'
      4. Hot-reloads synonyms into memory
      5. Clears search cache

    The synonym is active immediately — no server restart needed.

    Response (200)
    --------------
    { "status": "ok", "variant": "grdiner", "canonical": "grinder",
      "synonyms_loaded": 25 }

    Response (404 / 409)
    --------------------
    { "error": "..." }
    """
    result = approve_suggestion(suggestion_id)

    if "error" in result:
        return jsonify(result), result.pop("code", 400)

    # Hot-reload + cache clear
    count = reload_synonyms()
    search_cache.clear()

    result["synonyms_loaded"] = count
    return jsonify(result)


# ── POST /api/synonyms/reject/<id> ────────────────────────────────────────────

@synonym_bp.route("/api/synonyms/reject/<int:suggestion_id>", methods=["POST"])
def api_reject_suggestion(suggestion_id: int):
    """
    POST /api/synonyms/reject/<id>

    Marks a pending suggestion as 'rejected'.
    Does NOT modify the synonyms table or reload memory.

    Response (200)
    --------------
    { "status": "ok", "rejected_id": 1, "variant": "grdiner" }
    """
    result = reject_suggestion(suggestion_id)

    if "error" in result:
        return jsonify(result), result.pop("code", 400)

    return jsonify(result)
