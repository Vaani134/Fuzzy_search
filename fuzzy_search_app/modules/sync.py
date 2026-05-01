"""
modules/sync.py
---------------
Module 1 — MySQL → SQLite sync.

Fetches data from MySQL in batches and upserts into SQLite.
Supports:
  - Full sync  (wipes and reloads all rows)
  - Delta sync (only rows updated since last sync, using updated_at)

Live state is tracked in SYNC_STATE so the frontend can poll progress.
"""

import sqlite3
import sys
import os
import threading
from datetime import datetime, timezone
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import MYSQL_CONFIG, SYNC_TABLES, SYNC_BATCH_SIZE
from db.database import get_connection

try:
    import pymysql
    PYMYSQL_AVAILABLE = True
except ImportError:
    PYMYSQL_AVAILABLE = False


# ── Live sync state (in-memory, polled by frontend) ───────────────────────────
# Structure:
#   SYNC_STATE = {
#     "running":   bool,
#     "started_at": ISO str | None,
#     "finished_at": ISO str | None,
#     "mode":      "full" | "delta",
#     "tables": {
#       "products": {
#         "status":  "pending"|"running"|"ok"|"error",
#         "rows":    int,
#         "error":   str | None,
#         "started_at":  ISO str | None,
#         "finished_at": ISO str | None,
#       }, ...
#     }
#   }

_state_lock = threading.Lock()

SYNC_STATE: dict = {
    "running":     False,
    "started_at":  None,
    "finished_at": None,
    "mode":        None,
    "tables":      {},
}


def _init_state(mode: str):
    with _state_lock:
        SYNC_STATE["running"]     = True
        SYNC_STATE["started_at"]  = datetime.now(timezone.utc).isoformat()
        SYNC_STATE["finished_at"] = None
        SYNC_STATE["mode"]        = mode
        SYNC_STATE["tables"]      = {
            t: {"status": "pending", "rows": 0, "error": None,
                "started_at": None, "finished_at": None}
            for t in SYNC_TABLES
        }


def _update_table_state(table: str, **kwargs):
    with _state_lock:
        if table in SYNC_STATE["tables"]:
            SYNC_STATE["tables"][table].update(kwargs)


def _finish_state():
    with _state_lock:
        SYNC_STATE["running"]     = False
        SYNC_STATE["finished_at"] = datetime.now(timezone.utc).isoformat()


def get_live_state() -> dict:
    """Return a copy of the current sync state (thread-safe)."""
    with _state_lock:
        import copy
        return copy.deepcopy(SYNC_STATE)


# ── Column definitions per table ──────────────────────────────────────────────
TABLE_COLUMNS = {
    "brands": [
        "id", "business_id", "name", "description",
        "created_by", "deleted_at", "created_at", "updated_at",
    ],
    "categories": [
        "id", "name", "business_id", "short_code", "parent_id",
        "created_by", "category_type", "description", "slug",
        "deleted_at", "created_at", "updated_at",
    ],
    "product_group": [
        "id", "name", "created_by", "created_at", "updated_at",
    ],
    "products": [
        "id", "name", "item_code", "business_id", "type",
        "brand_id", "category_id", "sub_category_id",
        "sku", "sku2", "sku3", "barcode_type",
        "enable_stock", "alert_quantity", "weight",
        "image", "main_image", "product_description",
        "product_custom_field1", "product_custom_field2",
        "product_custom_field3", "product_custom_field4",
        "srp", "sales_price", "is_inactive", "not_for_selling",
        "out_of_stock", "aisle", "rack", "shelf", "bin",
        "qty_box", "case_qty", "master_case_qty", "ml",
        "product_group_id", "group_variation_name", "note",
        "created_by", "created_at", "updated_at", "synced_at",
    ],
    "transactions": [
        "id", "business_id", "location_id", "type", "sub_type",
        "status", "payment_status", "contact_id",
        "invoice_no", "ref_no", "transaction_date",
        "total_before_tax", "tax_amount", "discount_type",
        "discount_amount", "shipping_charges", "final_total",
        "sub_total", "item_qty", "total_qty",
        "additional_notes", "staff_note",
        "is_direct_sale", "is_suspend",
        "delivery_method", "delivery_date",
        "created_by", "created_at", "updated_at",
    ],
    "transaction_sell_lines": [
        "id", "transaction_id", "product_id", "variation_id",
        "quantity", "quantity_returned",
        "unit_price_before_discount", "unit_price",
        "line_discount_type", "line_discount_amount",
        "unit_price_inc_tax", "item_tax", "tax_id",
        "sell_line_note", "purchase_price",
        "out_of_stock", "is_picked", "is_packed",
        "created_at", "updated_at",
    ],
}


def _get_mysql_conn():
    """Open and return a PyMySQL connection using current saved settings."""
    if not PYMYSQL_AVAILABLE:
        raise RuntimeError("pymysql is not installed. Run: pip install pymysql")
    try:
        from modules.settings_manager import get_mysql_config
        cfg = get_mysql_config()
    except Exception:
        cfg = MYSQL_CONFIG
    return pymysql.connect(
        host=cfg["host"],
        port=cfg["port"],
        user=cfg["user"],
        password=cfg["password"],
        database=cfg["database"],
        charset=cfg.get("charset", "utf8mb4"),
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=10,
    )


def _sanitize(value):
    import decimal
    import datetime as dt
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _upsert_rows(sqlite_conn: sqlite3.Connection, table: str, rows: list) -> int:
    if not rows:
        return 0
    cols = TABLE_COLUMNS[table]
    placeholders = ", ".join(["?"] * len(cols))
    col_names    = ", ".join(cols)
    sql = f"INSERT OR REPLACE INTO {table} ({col_names}) VALUES ({placeholders})"
    data = [tuple(_sanitize(row.get(c)) for c in cols) for row in rows]
    sqlite_conn.executemany(sql, data)
    return len(data)


def _log_sync(sqlite_conn: sqlite3.Connection, table: str,
              count: int, status: str, error: Optional[str] = None):
    """Insert a new sync_log row — always inserts, never updates."""
    sqlite_conn.execute(
        """INSERT INTO sync_log
               (table_name, last_synced, records_synced, status, error_msg)
           VALUES (?, ?, ?, ?, ?)""",
        (table, datetime.now(timezone.utc).isoformat(), count, status, error),
    )


def sync_table(table: str, full: bool = True, since: Optional[str] = None) -> dict:
    """Sync a single table from MySQL → SQLite."""
    result = {"table": table, "rows_synced": 0, "status": "ok", "error": None}

    mysql_conn  = None
    sqlite_conn = None

    _update_table_state(table, status="running",
                        started_at=datetime.now(timezone.utc).isoformat())

    try:
        mysql_conn  = _get_mysql_conn()
        sqlite_conn = get_connection()

        cols     = TABLE_COLUMNS[table]
        col_list = ", ".join(f"`{c}`" for c in cols)

        where  = ""
        params = []
        if since:
            where  = "WHERE updated_at >= %s"
            params = [since]
            full   = False

        sqlite_conn.execute("PRAGMA foreign_keys = OFF")

        if full:
            sqlite_conn.execute(f"DELETE FROM {table}")

        offset = 0
        total  = 0

        with mysql_conn.cursor() as cursor:
            while True:
                query = (
                    f"SELECT {col_list} FROM `{table}` {where} "
                    f"LIMIT {SYNC_BATCH_SIZE} OFFSET {offset}"
                )
                cursor.execute(query, params)
                rows = cursor.fetchall()
                if not rows:
                    break

                count  = _upsert_rows(sqlite_conn, table, rows)
                total += count
                offset += SYNC_BATCH_SIZE

                # Commit every batch so progress is visible immediately
                sqlite_conn.commit()

                # Update live state
                _update_table_state(table, rows=total)
                print(f"  [{table}] synced {total} rows so far…")

        _log_sync(sqlite_conn, table, total, "ok")
        sqlite_conn.commit()
        sqlite_conn.execute("PRAGMA foreign_keys = ON")
        result["rows_synced"] = total

        _update_table_state(table, status="ok", rows=total,
                            finished_at=datetime.now(timezone.utc).isoformat())

    except Exception as exc:
        result["status"] = "error"
        result["error"]  = str(exc)
        _update_table_state(table, status="error", error=str(exc),
                            finished_at=datetime.now(timezone.utc).isoformat())
        try:
            _log_sync(sqlite_conn, table, 0, "error", str(exc))
            sqlite_conn.commit()
        except Exception:
            pass
        print(f"  [ERROR] {table}: {exc}")

    finally:
        try:
            if mysql_conn:
                mysql_conn.close()
        except Exception:
            pass
        try:
            if sqlite_conn:
                sqlite_conn.execute("PRAGMA foreign_keys = ON")
                sqlite_conn.close()
        except Exception:
            pass

    return result


def sync_all(full: bool = True) -> list:
    """Sync all tables in dependency order."""
    _init_state("full" if full else "delta")
    print(f"\n{'='*50}")
    print(f"Starting {'FULL' if full else 'DELTA'} sync — {datetime.now()}")
    print(f"{'='*50}")

    results = []
    for table in SYNC_TABLES:
        print(f"\n→ Syncing table: {table}")
        res = sync_table(table, full=full)
        results.append(res)
        icon = "✓" if res["status"] == "ok" else "✗"
        print(f"  {icon} {table}: {res['rows_synced']} rows — {res['status']}")

    _finish_state()
    print(f"\n{'='*50}\nSync complete.")
    return results


def sync_all_background(full: bool = True, callback=None):
    """
    Run sync_all in a background thread.
    Optional callback(results) called when done.
    Returns the Thread object immediately.
    """
    def _run():
        results = sync_all(full=full)
        if callback:
            try:
                callback(results)
            except Exception as e:
                print(f"[sync callback error] {e}")

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t


def get_sync_status() -> list:
    """
    Return the LATEST sync log entry per table, sorted newest first.
    Always opens a fresh connection — never cached.
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT s.*
            FROM sync_log s
            INNER JOIN (
                SELECT table_name, MAX(id) AS max_id
                FROM sync_log
                GROUP BY table_name
            ) latest ON s.table_name = latest.table_name
                     AND s.id        = latest.max_id
            ORDER BY s.last_synced DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_sync_history(limit: int = 50) -> list:
    """Return the last `limit` sync log entries across all tables, newest first."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT * FROM sync_log
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()



# ── Column definitions per table ──────────────────────────────────────────────
# Only the columns that exist in BOTH MySQL and our SQLite schema.
TABLE_COLUMNS = {
    "brands": [
        "id", "business_id", "name", "description",
        "created_by", "deleted_at", "created_at", "updated_at",
    ],
    "categories": [
        "id", "name", "business_id", "short_code", "parent_id",
        "created_by", "category_type", "description", "slug",
        "deleted_at", "created_at", "updated_at",
    ],
    "product_group": [
        "id", "name", "created_by", "created_at", "updated_at",
    ],
    "products": [
        "id", "name", "item_code", "business_id", "type",
        "brand_id", "category_id", "sub_category_id",
        "sku", "sku2", "sku3", "barcode_type",
        "enable_stock", "alert_quantity", "weight",
        "image", "main_image", "product_description",
        "product_custom_field1", "product_custom_field2",
        "product_custom_field3", "product_custom_field4",
        "srp", "sales_price", "is_inactive", "not_for_selling",
        "out_of_stock", "aisle", "rack", "shelf", "bin",
        "qty_box", "case_qty", "master_case_qty", "ml",
        "product_group_id", "group_variation_name", "note",
        "created_by", "created_at", "updated_at", "synced_at",
    ],
    "transactions": [
        "id", "business_id", "location_id", "type", "sub_type",
        "status", "payment_status", "contact_id",
        "invoice_no", "ref_no", "transaction_date",
        "total_before_tax", "tax_amount", "discount_type",
        "discount_amount", "shipping_charges", "final_total",
        "sub_total", "item_qty", "total_qty",
        "additional_notes", "staff_note",
        "is_direct_sale", "is_suspend",
        "delivery_method", "delivery_date",
        "created_by", "created_at", "updated_at",
    ],
    "transaction_sell_lines": [
        "id", "transaction_id", "product_id", "variation_id",
        "quantity", "quantity_returned",
        "unit_price_before_discount", "unit_price",
        "line_discount_type", "line_discount_amount",
        "unit_price_inc_tax", "item_tax", "tax_id",
        "sell_line_note", "purchase_price",
        "out_of_stock", "is_picked", "is_packed",
        "created_at", "updated_at",
    ],
}


def _get_mysql_conn():
    """Open and return a PyMySQL connection using current saved settings."""
    if not PYMYSQL_AVAILABLE:
        raise RuntimeError("pymysql is not installed. Run: pip install pymysql")
    try:
        from modules.settings_manager import get_mysql_config
        cfg = get_mysql_config()
    except Exception:
        cfg = MYSQL_CONFIG   # fallback to config.py values
    return pymysql.connect(
        host=cfg["host"],
        port=cfg["port"],
        user=cfg["user"],
        password=cfg["password"],
        database=cfg["database"],
        charset=cfg.get("charset", "utf8mb4"),
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=10,
    )


def _sanitize(value):
    """
    Convert MySQL types that SQLite cannot bind natively:
      - decimal.Decimal  → float
      - datetime / date  → ISO string
      - bytes            → str (utf-8 decode)
    Everything else passes through unchanged.
    """
    import decimal
    import datetime as dt

    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _upsert_rows(sqlite_conn: sqlite3.Connection, table: str, rows: list) -> int:
    """INSERT OR REPLACE rows into SQLite. Returns count inserted."""
    if not rows:
        return 0
    cols = TABLE_COLUMNS[table]
    placeholders = ", ".join(["?"] * len(cols))
    col_names    = ", ".join(cols)
    sql = f"INSERT OR REPLACE INTO {table} ({col_names}) VALUES ({placeholders})"

    data = []
    for row in rows:
        data.append(tuple(_sanitize(row.get(c)) for c in cols))

    sqlite_conn.executemany(sql, data)
    return len(data)


def _log_sync(sqlite_conn: sqlite3.Connection, table: str,
              count: int, status: str, error: Optional[str] = None):
    sqlite_conn.execute(
        """INSERT INTO sync_log (table_name, last_synced, records_synced, status, error_msg)
           VALUES (?, ?, ?, ?, ?)""",
        (table, datetime.now(timezone.utc).isoformat(), count, status, error),
    )


def sync_table(table: str, full: bool = True, since: Optional[str] = None) -> dict:
    """
    Sync a single table from MySQL to SQLite.

    Parameters
    ----------
    table : str   — table name
    full  : bool  — if True, truncate SQLite table first (full reload)
    since : str   — ISO datetime string; if provided, only fetch rows where
                    updated_at >= since (delta sync, overrides full=True)

    Returns dict with keys: table, rows_synced, status, error
    """
    result = {"table": table, "rows_synced": 0, "status": "ok", "error": None}

    mysql_conn  = None
    sqlite_conn = None

    try:
        mysql_conn  = _get_mysql_conn()
        sqlite_conn = get_connection()

        cols = TABLE_COLUMNS[table]
        col_list = ", ".join(f"`{c}`" for c in cols)

        # Build WHERE clause for delta sync
        where = ""
        params = []
        if since:
            where = "WHERE updated_at >= %s"
            params = [since]
            full = False   # delta mode — don't truncate

        # Disable FK checks for the duration of this sync
        sqlite_conn.execute("PRAGMA foreign_keys = OFF")

        if full:
            sqlite_conn.execute(f"DELETE FROM {table}")

        offset = 0
        total  = 0

        with mysql_conn.cursor() as cursor:
            while True:
                query = (
                    f"SELECT {col_list} FROM `{table}` {where} "
                    f"LIMIT {SYNC_BATCH_SIZE} OFFSET {offset}"
                )
                cursor.execute(query, params)
                rows = cursor.fetchall()
                if not rows:
                    break

                count = _upsert_rows(sqlite_conn, table, rows)
                total  += count
                offset += SYNC_BATCH_SIZE

                print(f"  [{table}] synced {total} rows so far…")

        _log_sync(sqlite_conn, table, total, "ok")
        sqlite_conn.commit()
        # Re-enable FK checks after sync
        sqlite_conn.execute("PRAGMA foreign_keys = ON")
        result["rows_synced"] = total

    except Exception as exc:
        result["status"] = "error"
        result["error"]  = str(exc)
        try:
            _log_sync(sqlite_conn, table, 0, "error", str(exc))
            sqlite_conn.commit()
        except Exception:
            pass
        print(f"  [ERROR] {table}: {exc}")

    finally:
        try:
            if mysql_conn:
                mysql_conn.close()
        except Exception:
            pass
        try:
            if sqlite_conn:
                sqlite_conn.execute("PRAGMA foreign_keys = ON")
                sqlite_conn.close()
        except Exception:
            pass

    return result


def sync_all(full: bool = True) -> list:
    """
    Sync all tables in dependency order.
    Returns list of per-table result dicts.
    """
    print(f"\n{'='*50}")
    print(f"Starting {'FULL' if full else 'DELTA'} sync — {datetime.now()}")
    print(f"{'='*50}")

    results = []
    for table in SYNC_TABLES:
        print(f"\n→ Syncing table: {table}")
        res = sync_table(table, full=full)
        results.append(res)
        status_icon = "✓" if res["status"] == "ok" else "✗"
        print(f"  {status_icon} {table}: {res['rows_synced']} rows — {res['status']}")

    print(f"\n{'='*50}")
    print("Sync complete.")
    return results


def get_sync_status() -> list:
    """Return the latest sync log entry per table."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT s.*
            FROM sync_log s
            INNER JOIN (
                SELECT table_name, MAX(id) AS max_id
                FROM sync_log
                GROUP BY table_name
            ) latest ON s.table_name = latest.table_name AND s.id = latest.max_id
            ORDER BY s.table_name
            """
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
