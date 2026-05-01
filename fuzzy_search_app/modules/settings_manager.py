"""
modules/settings_manager.py
----------------------------
Reads and writes database connection settings to db_settings.json.
The running app calls get_mysql_config() instead of reading config.py
directly, so changes take effect without a server restart.
"""

import json
import os

SETTINGS_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "db_settings.json",
)

DEFAULTS = {
    "host":     "127.0.0.1",
    "port":     "3306",
    "database": "cloudbbtl_novx",
    "user":     "root",
    "password": "",
}


def load() -> dict:
    """Return current settings, falling back to defaults for missing keys."""
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Merge with defaults so new keys are always present
        return {**DEFAULTS, **data}
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(DEFAULTS)


def save(settings: dict) -> None:
    """Persist settings to disk."""
    # Only store the known keys — never write arbitrary data
    safe = {k: str(settings.get(k, DEFAULTS[k])) for k in DEFAULTS}
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(safe, f, indent=2)


def get_mysql_config() -> dict:
    """Return a pymysql-compatible config dict from current settings."""
    s = load()
    return {
        "host":     s["host"].strip(),
        "port":     int(s["port"] or 3306),
        "user":     s["user"].strip(),
        "password": s["password"],
        "database": s["database"].strip(),
        "charset":  "utf8mb4",
    }


def test_connection(host: str, port: int, user: str,
                    password: str, database: str) -> dict:
    """
    Try to open a MySQL connection with the given credentials.
    Returns {"ok": True/False, "message": str, "detail": str}
    """
    try:
        import pymysql
    except ImportError:
        return {
            "ok":      False,
            "message": "pymysql not installed",
            "detail":  "Run: pip install pymysql",
        }

    try:
        conn = pymysql.connect(
            host=host.strip(),
            port=int(port),
            user=user.strip(),
            password=password,
            database=database.strip(),
            charset="utf8mb4",
            connect_timeout=6,
        )
        # Quick sanity check — fetch server version
        with conn.cursor() as cur:
            cur.execute("SELECT VERSION()")
            version = cur.fetchone()[0]
        conn.close()
        return {
            "ok":      True,
            "message": "Connection successful",
            "detail":  f"MySQL {version} — database '{database}' is accessible.",
        }
    except pymysql.err.OperationalError as e:
        code, msg = e.args
        return {
            "ok":      False,
            "message": f"Connection failed (MySQL error {code})",
            "detail":  msg,
        }
    except Exception as e:
        return {
            "ok":      False,
            "message": "Connection failed",
            "detail":  str(e),
        }
