"""
config.py
---------
Central configuration for the Fuzzy Search App.
Edit MySQL credentials and paths here.
"""

import os

# ── MySQL (source) ─────────────────────────────────────────────────────────────
MYSQL_CONFIG = {
    "host":     os.getenv("MYSQL_HOST",     "127.0.0.1"),
    "port":     int(os.getenv("MYSQL_PORT", "3306")),
    "user":     os.getenv("MYSQL_USER",     "root"),
    "password": os.getenv("MYSQL_PASSWORD", ""),
    "database": os.getenv("MYSQL_DATABASE", "cloudbbtl_novx"),
    "charset":  "utf8mb4",
}
# ── NOTE ──────────────────────────────────────────────────────────────────────
# Database: cloudbbtl_novx  |  Host: 127.0.0.1  |  User: root  |  Password: ""
# Override any value above via environment variables or a .env file.

# ── SQLite (local cache) ───────────────────────────────────────────────────────
BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
SQLITE_PATH = os.getenv("SQLITE_PATH", os.path.join(BASE_DIR, "db", "local.db"))
SCHEMA_PATH = os.path.join(BASE_DIR, "db", "schema.sql")

# ── Sync settings ──────────────────────────────────────────────────────────────
# Tables to sync from MySQL → SQLite, in dependency order
SYNC_TABLES = [
    "brands",
    "categories",
    "product_group",
    "products",
    "transactions",
    "transaction_sell_lines",
]

# How many rows to fetch per batch during sync
# 2000 rows per batch = ~244 round trips for 488k transaction_sell_lines rows
# (vs 976 round trips at 500). Fewer connections = less chance of timeout.
SYNC_BATCH_SIZE = 2000

# ── Search settings ────────────────────────────────────────────────────────────
SEARCH_MIN_SCORE  = 35.0   # discard results below this score
SEARCH_DEFAULT_K  = 20     # default number of results to return
SEARCH_MAX_K      = 100    # hard cap

# Score thresholds for UI badge colours
SCORE_HIGH   = 70
SCORE_MEDIUM = 50
SCORE_LOW    = 35

# ── Image CDN ─────────────────────────────────────────────────────────────────
# Base URL prepended to relative image paths stored in the database.
# DB stores:  /img/chinahosmall_p1.jpg  or  img/chinahosmall_p1.jpg
# Full URL:   https://novxcloud.com/uploads/img/chinahosmall_p1.jpg
IMAGE_BASE_URL = os.getenv("IMAGE_BASE_URL", "https://novxcloud.com")

# ── Flask ──────────────────────────────────────────────────────────────────────
SECRET_KEY = os.getenv("SECRET_KEY", "change-me-in-production")
DEBUG      = os.getenv("FLASK_DEBUG", "true").lower() == "true"
HOST       = os.getenv("FLASK_HOST", "0.0.0.0")
PORT       = int(os.getenv("FLASK_PORT", "5000"))

# ── Redis (optional search result cache) ──────────────────────────────────────
# When REDIS_URL is set and Redis is reachable, SearchCache uses Redis.
# When Redis is unavailable or REDIS_URL is blank, it falls back to the
# in-memory dict cache automatically — no code changes needed.
REDIS_URL = os.getenv("REDIS_URL", "")          # e.g. redis://127.0.0.1:6379/0
REDIS_KEY_PREFIX = os.getenv("REDIS_KEY_PREFIX", "fzsearch:")  # namespace prefix
