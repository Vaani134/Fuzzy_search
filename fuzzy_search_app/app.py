"""
app.py
------
Flask application — entry point.

Routes
------
  GET  /                        — dashboard
  GET  /search                  — search UI
  GET  /api/search?q=&k=        — JSON search API
  GET  /api/product/<id>        — product detail JSON
  POST /api/sync                — trigger MySQL → SQLite sync
  GET  /api/sync/status         — sync log
  POST /api/search/rebuild      — rebuild in-memory index
  GET  /api/stats               — engine stats
"""

import os
import sys
from datetime import datetime

from flask import Flask, render_template, request, jsonify, abort, send_file

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import SECRET_KEY, DEBUG, HOST, PORT, SEARCH_DEFAULT_K, SYNC_TABLES
from db.database import init_db, get_connection, dict_from_row
from modules.fuzzy_search import get_engine
from modules.sync import sync_all, sync_all_background, sync_table, get_sync_status, get_sync_history, get_live_state
from modules.autocomplete import get_suggestions
from modules.zip_builder import build_zip
from modules.settings_manager import (
    load as load_settings,
    save as save_settings,
    test_connection,
    get_mysql_config,
)

# ── App setup ──────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = SECRET_KEY


# ── Jinja filter: resolve product image path → full URL ───────────────────────
@app.template_filter("img_url")
def img_url_filter(path: str) -> str:
    """
    Convert a DB image path to a full URL.

    Real DB values observed:
      image      column: /uploads/img/chinahosmall_p1.jpg  ← has /uploads/
      main_image column: /img/chinahosmall_p1.jpg          ← missing /uploads/

    Both must resolve to:
      https://novxcloud.com/uploads/img/<filename>
    """
    if not path:
        return ""
    # Already a full URL — return unchanged
    if path.startswith("http://") or path.startswith("https://"):
        return path

    clean = path.lstrip("/")   # remove leading slash

    # Case 1: /uploads/img/file.jpg  →  uploads/img/file.jpg  (already correct)
    if clean.startswith("uploads/"):
        return f"https://novxcloud.com/{clean}"

    # Case 2: /img/file.jpg  →  img/file.jpg  (missing /uploads prefix)
    if clean.startswith("img/"):
        return f"https://novxcloud.com/uploads/{clean}"

    # Case 3: bare filename  →  assume uploads/img/
    if "/" not in clean:
        return f"https://novxcloud.com/uploads/img/{clean}"

    # Case 4: any other relative path — prepend domain as-is
    return f"https://novxcloud.com/{clean}"

# Initialise SQLite schema on startup
init_db()

# Initialise search engine (auto-rebuild every 5 minutes)
engine = get_engine(rebuild_interval=300)


# ── UI Routes ──────────────────────────────────────────────────────────────────

@app.route("/")
def dashboard():
    """Dashboard — counts, top categories, recent sync status."""
    conn = get_connection()
    try:
        total_products   = conn.execute(
            "SELECT COUNT(*) FROM products WHERE is_inactive=0"
        ).fetchone()[0]
        total_all_products = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        total_categories = conn.execute("SELECT COUNT(*) FROM categories WHERE deleted_at IS NULL").fetchone()[0]
        total_brands     = conn.execute("SELECT COUNT(*) FROM brands WHERE deleted_at IS NULL").fetchone()[0]
        total_groups     = conn.execute("SELECT COUNT(*) FROM product_group").fetchone()[0]
        total_transactions = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        out_of_stock     = conn.execute(
            "SELECT COUNT(*) FROM products WHERE out_of_stock=1 AND is_inactive=0"
        ).fetchone()[0]
        not_for_selling  = conn.execute(
            "SELECT COUNT(*) FROM products WHERE not_for_selling=1 AND is_inactive=0"
        ).fetchone()[0]
        inactive_products = conn.execute(
            "SELECT COUNT(*) FROM products WHERE is_inactive=1"
        ).fetchone()[0]

        # Top 10 categories by product count
        top_categories = conn.execute(
            """
            SELECT c.name, COUNT(p.id) AS product_count
            FROM categories c
            LEFT JOIN products p ON p.category_id = c.id
                AND p.is_inactive = 0 AND p.not_for_selling = 0
            WHERE c.deleted_at IS NULL
            GROUP BY c.id, c.name
            ORDER BY product_count DESC
            LIMIT 10
            """
        ).fetchall()

        # Top 10 brands by product count
        top_brands = conn.execute(
            """
            SELECT b.name, COUNT(p.id) AS product_count
            FROM brands b
            LEFT JOIN products p ON p.brand_id = b.id
                AND p.is_inactive = 0 AND p.not_for_selling = 0
            WHERE b.deleted_at IS NULL
            GROUP BY b.id, b.name
            ORDER BY product_count DESC
            LIMIT 10
            """
        ).fetchall()

        # Recent transactions (last 5)
        recent_transactions = conn.execute(
            """
            SELECT id, invoice_no, type, status, payment_status,
                   final_total, transaction_date
            FROM transactions
            ORDER BY id DESC
            LIMIT 5
            """
        ).fetchall()

    finally:
        conn.close()

    sync_status = get_sync_status()
    eng_stats   = engine.stats()

    return render_template(
        "dashboard.html",
        total_products=total_products,
        total_all_products=total_all_products,
        total_categories=total_categories,
        total_brands=total_brands,
        total_groups=total_groups,
        total_transactions=total_transactions,
        out_of_stock=out_of_stock,
        not_for_selling=not_for_selling,
        inactive_products=inactive_products,
        top_categories=[dict(r) for r in top_categories],
        top_brands=[dict(r) for r in top_brands],
        recent_transactions=[dict(r) for r in recent_transactions],
        sync_status=sync_status,
        eng_stats=eng_stats,
        now=datetime.now(),
    )


@app.route("/search")
def index():
    """Main search page."""
    query   = request.args.get("q", "").strip()
    top_k   = int(request.args.get("k", SEARCH_DEFAULT_K))
    results = []
    stats   = engine.stats()

    if query:
        results = engine.search_with_field_scores(query, top_k=top_k)

    return render_template(
        "index.html",
        query=query,
        results=results,
        stats=stats,
        top_k=top_k,
        now=datetime.now(),
    )


@app.route("/product/<int:product_id>")
def product_detail(product_id: int):
    """Product detail page."""
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT p.*,
                   COALESCE(b.name, '')  AS brand_name,
                   COALESCE(c.name, '')  AS category_name,
                   COALESCE(pg.name, '') AS group_name
            FROM products p
            LEFT JOIN brands        b  ON b.id  = p.brand_id
            LEFT JOIN categories    c  ON c.id  = p.category_id
            LEFT JOIN product_group pg ON pg.id = p.product_group_id
            WHERE p.id = ?
            """,
            (product_id,),
        ).fetchone()
    finally:
        conn.close()

    if not row:
        abort(404)

    product = dict_from_row(row)
    return render_template("product.html", product=product)


@app.route("/sync")
def sync_page():
    """Sync management page."""
    status = get_sync_status()
    return render_template("sync.html", sync_status=status, now=datetime.now())


# ── JSON API ───────────────────────────────────────────────────────────────────

@app.route("/api/search")
def api_search():
    """
    GET /api/search?q=hookah&k=20
    Returns JSON list of matching products.
    """
    query = request.args.get("q", "").strip()
    top_k = int(request.args.get("k", SEARCH_DEFAULT_K))

    if not query:
        return jsonify({"error": "q parameter is required"}), 400

    results = engine.search_with_field_scores(query, top_k=top_k)
    return jsonify({
        "query":   query,
        "count":   len(results),
        "results": results,
    })


@app.route("/api/product/<int:product_id>")
def api_product(product_id: int):
    """GET /api/product/<id> — product detail JSON."""
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT p.*,
                   COALESCE(b.name, '')  AS brand_name,
                   COALESCE(c.name, '')  AS category_name,
                   COALESCE(pg.name, '') AS group_name
            FROM products p
            LEFT JOIN brands        b  ON b.id  = p.brand_id
            LEFT JOIN categories    c  ON c.id  = p.category_id
            LEFT JOIN product_group pg ON pg.id = p.product_group_id
            WHERE p.id = ?
            """,
            (product_id,),
        ).fetchone()
    finally:
        conn.close()

    if not row:
        return jsonify({"error": "Product not found"}), 404

    return jsonify(dict_from_row(row))


@app.route("/api/sync", methods=["POST"])
def api_sync():
    """
    POST /api/sync
    Body (JSON, optional): {"full": true, "tables": ["products"]}

    Starts sync in a background thread and returns immediately.
    Poll GET /api/sync/live for real-time progress.
    """
    from modules.sync import get_live_state as _live
    body = request.get_json(silent=True) or {}
    full = body.get("full", True)
    tables = body.get("tables", None)

    # Reject if already running
    state = _live()
    if state["running"]:
        return jsonify({"status": "already_running",
                        "message": "A sync is already in progress."}), 409

    def _after_sync(results):
        engine.rebuild()

    if tables:
        # Single-table sync — run synchronously (fast tables only)
        valid = [t for t in tables if t in SYNC_TABLES]
        results = [sync_table(t, full=full) for t in valid]
        engine.rebuild()
        return jsonify({"status": "ok", "results": results,
                        "indexed": engine.stats()["total_products"]})

    # Full / delta sync — background thread
    sync_all_background(full=full, callback=_after_sync)
    return jsonify({"status": "started",
                    "message": "Sync started in background. Poll /api/sync/live for progress."})


@app.route("/api/sync/live")
def api_sync_live():
    """GET /api/sync/live — real-time sync progress (poll every 2s)."""
    return jsonify(get_live_state())


@app.route("/api/sync/history")
def api_sync_history():
    """GET /api/sync/history?limit=50 — full sync log history, newest first."""
    limit = min(int(request.args.get("limit", 50)), 200)
    return jsonify(get_sync_history(limit=limit))


@app.route("/api/sync/status")
def api_sync_status():
    """GET /api/sync/status — latest sync log per table."""
    return jsonify(get_sync_status())


@app.route("/api/search/rebuild", methods=["POST"])
def api_rebuild():
    """POST /api/search/rebuild — rebuild in-memory search index from SQLite."""
    count = engine.rebuild()
    return jsonify({"status": "ok", "indexed": count})


@app.route("/api/stats")
def api_stats():
    """GET /api/stats — engine and DB stats."""
    stats = engine.stats()
    conn  = get_connection()
    try:
        product_count = conn.execute(
            "SELECT COUNT(*) FROM products WHERE is_inactive=0"
        ).fetchone()[0]
        brand_count    = conn.execute("SELECT COUNT(*) FROM brands").fetchone()[0]
        category_count = conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
    finally:
        conn.close()

    return jsonify({
        **stats,
        "db_products":   product_count,
        "db_brands":     brand_count,
        "db_categories": category_count,
    })


# ── Autocomplete API ───────────────────────────────────────────────────────────

@app.route("/api/autocomplete")
def api_autocomplete():
    """
    GET /api/autocomplete?q=gla&limit=10
    Returns JSON list of search suggestions.
    """
    query = request.args.get("q", "").strip()
    limit = min(int(request.args.get("limit", 10)), 20)

    if not query or len(query) < 2:
        return jsonify([])

    suggestions = get_suggestions(query, limit=limit)
    return jsonify(suggestions)


# ── ZIP Download API ───────────────────────────────────────────────────────────

@app.route("/api/download-zip", methods=["POST"])
def api_download_zip():
    """
    POST /api/download-zip
    Body: {"product_ids": [1, 2, 3, ...]}

    Single product  → proxies the image file directly (no ZIP overhead).
    Multiple products → builds and returns a ZIP.
    """
    body = request.get_json(silent=True) or {}
    product_ids = body.get("product_ids", [])

    if not product_ids:
        return jsonify({"error": "product_ids is required"}), 400

    if len(product_ids) > 200:
        return jsonify({"error": "Maximum 200 products per download"}), 400

    # Fetch product image data from SQLite
    conn = get_connection()
    try:
        placeholders = ",".join("?" * len(product_ids))
        rows = conn.execute(
            f"SELECT id, name, image, main_image FROM products WHERE id IN ({placeholders})",
            product_ids,
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return jsonify({"error": "No products found"}), 404

    products = [dict(r) for r in rows]

    # ── Single image — proxy directly, no ZIP ─────────────────────────────────
    if len(products) == 1:
        from modules.zip_builder import resolve_image_url
        import urllib.request as urlreq
        import urllib.error
        import os

        p   = products[0]
        raw = p.get("main_image") or p.get("image") or ""
        url = resolve_image_url(raw)

        if not url:
            return jsonify({"error": "No image available for this product"}), 404

        try:
            req = urlreq.Request(url, headers={"User-Agent": "Mozilla/5.0 FuzzySearch/1.0"})
            with urlreq.urlopen(req, timeout=10) as resp:
                data     = resp.read()
                ctype    = resp.headers.get("Content-Type", "image/jpeg")

            # Build a clean filename from product name + extension
            ext = os.path.splitext(url.split("?")[0])[-1].lower()
            if ext not in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
                ext = ".jpg"
            import re
            safe_name = re.sub(r'[^\w\s-]', '', p.get("name", "image")).strip()
            safe_name = re.sub(r'\s+', '_', safe_name)[:80]
            filename  = f"{safe_name}{ext}"

            import io
            return send_file(
                io.BytesIO(data),
                mimetype=ctype,
                as_attachment=True,
                download_name=filename,
            )

        except urllib.error.HTTPError as e:
            return jsonify({"error": f"Image fetch failed: HTTP {e.code}"}), 502
        except Exception as e:
            return jsonify({"error": f"Image fetch failed: {e}"}), 502

    # ── Multiple images — build ZIP ────────────────────────────────────────────
    zip_buffer, stats = build_zip(products)

    if stats["downloaded"] == 0:
        return jsonify({
            "error": "No images could be downloaded",
            "stats": stats,
        }), 422

    filename = f"product_images_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"

    return send_file(
        zip_buffer,
        mimetype="application/zip",
        as_attachment=True,
        download_name=filename,
    )


# ── Settings page ─────────────────────────────────────────────────────────────

@app.route("/settings")
def settings_page():
    """Database configuration page."""
    return render_template("settings.html", settings=load_settings())


@app.route("/api/settings", methods=["GET"])
def api_settings_get():
    """GET /api/settings — return current DB settings (password masked)."""
    s = load_settings()
    s["password"] = "••••••••" if s["password"] else ""
    return jsonify(s)


@app.route("/api/settings", methods=["POST"])
def api_settings_save():
    """
    POST /api/settings
    Body: {host, port, database, user, password}
    Saves to db_settings.json.  Empty password keeps the existing one.
    """
    body = request.get_json(silent=True) or {}

    # Load existing so we can preserve password if not provided
    current = load_settings()

    new_settings = {
        "host":     body.get("host",     current["host"]).strip(),
        "port":     str(body.get("port", current["port"])).strip(),
        "database": body.get("database", current["database"]).strip(),
        "user":     body.get("user",     current["user"]).strip(),
        "password": body.get("password", current["password"]),
    }

    # If password field was sent as the masked placeholder, keep existing
    if new_settings["password"] in ("••••••••", ""):
        if body.get("password", "") in ("••••••••", ""):
            new_settings["password"] = current["password"]

    save_settings(new_settings)
    return jsonify({"status": "ok", "message": "Settings saved successfully."})


@app.route("/api/settings/test", methods=["POST"])
def api_settings_test():
    """
    POST /api/settings/test
    Body: {host, port, database, user, password}
    Tests the connection WITHOUT saving.
    """
    body = request.get_json(silent=True) or {}
    current = load_settings()

    host     = body.get("host",     current["host"]).strip()
    port     = int(body.get("port", current["port"]) or 3306)
    database = body.get("database", current["database"]).strip()
    user     = body.get("user",     current["user"]).strip()
    password = body.get("password", current["password"])

    # If masked placeholder sent, use stored password
    if password in ("••••••••",):
        password = current["password"]

    result = test_connection(host, port, user, password, database)
    return jsonify(result)


# ── Error handlers ─────────────────────────────────────────────────────────────

@app.errorhandler(404)
def not_found(e):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Not found"}), 404
    return render_template("404.html"), 404


@app.errorhandler(500)
def server_error(e):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Internal server error"}), 500
    return render_template("500.html"), 500


# ── Run ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(host=HOST, port=PORT, debug=DEBUG)
