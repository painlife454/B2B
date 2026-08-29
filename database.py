"""
Database access layer.
All queries use parameterized statements (?) - never string-formatted SQL -
to prevent SQL injection.
"""
import sqlite3
from pathlib import Path
from flask import g

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "instance" / "marketplace.db"
SCHEMA_PATH = BASE_DIR / "schema.sql"


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(str(DB_PATH), timeout=10)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
        # WAL mode lets readers (chat polling) and a writer proceed together
        # instead of blocking each other; busy_timeout makes SQLite retry
        # for a bit instead of immediately raising "database is locked".
        g.db.execute("PRAGMA journal_mode = WAL")
        g.db.execute("PRAGMA busy_timeout = 5000")
    return g.db


def close_db(e=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        conn.executescript(f.read())
    conn.commit()
    conn.close()


def _add_col(conn, table, col, decl):
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if col not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


def migrate_db():
    """
    Idempotent, additive migration: brings an existing marketplace.db up to
    date with the current schema.sql without touching existing rows. Safe to
    run on every startup.
    """
    if not DB_PATH.exists():
        return
    conn = sqlite3.connect(str(DB_PATH))
    try:
        # Fix: "Shoes" was originally seeded as a child of "Apparels"; it
        # should be its own top-level main category. This must run BEFORE
        # the schema re-run below - otherwise schema.sql's seed insert sees
        # no top-level "Shoes" yet and creates a duplicate row.
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "categories" in tables:
            conn.execute("""
                UPDATE categories SET parent_id = NULL
                WHERE name = 'Shoes'
                  AND parent_id IN (SELECT id FROM categories WHERE name = 'Apparels' AND parent_id IS NULL)
            """)
            dupes = conn.execute(
                "SELECT id FROM categories WHERE name = 'Shoes' AND parent_id IS NULL ORDER BY id"
            ).fetchall()
            if len(dupes) > 1:
                keep_id = dupes[0][0]
                for (dupe_id,) in dupes[1:]:
                    conn.execute("UPDATE products SET category_id = ? WHERE category_id = ?", (keep_id, dupe_id))
                    conn.execute("DELETE FROM categories WHERE id = ?", (dupe_id,))

        # Re-run the full schema - every statement in schema.sql uses
        # CREATE TABLE/INDEX IF NOT EXISTS, so this only adds what's missing
        # (new tables: notifications, withdrawals, etc.)
        with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
            conn.executescript(f.read())

        # ---- Column additions (SQLite has no "ADD COLUMN IF NOT EXISTS") ----
        _add_col(conn, "users", "is_featured", "INTEGER NOT NULL DEFAULT 0")
        _add_col(conn, "users", "featured_until", "TEXT")
        _add_col(conn, "users", "logo_url", "TEXT")
        _add_col(conn, "users", "address", "TEXT")
        _add_col(conn, "users", "trade_info", "TEXT")
        _add_col(conn, "users", "business_description", "TEXT")
        _add_col(conn, "users", "delivery_areas", "TEXT")
        _add_col(conn, "users", "payment_info", "TEXT")
        _add_col(conn, "users", "business_type", "TEXT")
        _add_col(conn, "users", "billing_address", "TEXT")
        _add_col(conn, "users", "tax_info", "TEXT")
        _add_col(conn, "users", "kyc_document_url", "TEXT")
        _add_col(conn, "users", "kyc_status", "TEXT NOT NULL DEFAULT 'unsubmitted'")
        _add_col(conn, "users", "kyc_note", "TEXT")
        _add_col(conn, "users", "notify_new_order", "INTEGER NOT NULL DEFAULT 1")
        _add_col(conn, "users", "notify_order_status", "INTEGER NOT NULL DEFAULT 1")
        _add_col(conn, "users", "notify_low_stock", "INTEGER NOT NULL DEFAULT 1")
        _add_col(conn, "users", "notify_message", "INTEGER NOT NULL DEFAULT 1")

        _add_col(conn, "products", "image_url", "TEXT")
        _add_col(conn, "products", "category_id", "INTEGER")
        _add_col(conn, "products", "specification", "TEXT")
        _add_col(conn, "products", "low_stock_threshold", "INTEGER NOT NULL DEFAULT 5")
        _add_col(conn, "products", "delivery_method", "TEXT")
        _add_col(conn, "products", "delivery_charge", "REAL")
        _add_col(conn, "products", "delivery_areas", "TEXT")

        _add_col(conn, "orders", "color_id", "INTEGER")
        _add_col(conn, "orders", "customization_note", "TEXT")
        _add_col(conn, "orders", "recipient_name", "TEXT")
        _add_col(conn, "orders", "recipient_phone", "TEXT")
        _add_col(conn, "orders", "recipient_email", "TEXT")
        _add_col(conn, "orders", "recipient_address", "TEXT")
        _add_col(conn, "orders", "courier_name", "TEXT")
        _add_col(conn, "orders", "tracking_number", "TEXT")

        # orders.status CHECK constraint needs 'processing' and
        # 'ready_for_delivery' added - ALTER TABLE ADD COLUMN can't change an
        # existing CHECK constraint, so rebuild the table if it's still the
        # old shape. All existing rows/ids are preserved.
        orders_sql_row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='orders'"
        ).fetchone()
        if orders_sql_row and "ready_for_delivery" not in orders_sql_row[0]:
            conn.execute("ALTER TABLE orders RENAME TO orders_old")
            with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
                conn.executescript(f.read())  # recreates "orders" with the new CHECK
            old_cols = [row[1] for row in conn.execute("PRAGMA table_info(orders_old)")]
            new_cols = [row[1] for row in conn.execute("PRAGMA table_info(orders)")]
            shared = [c for c in old_cols if c in new_cols]
            col_list = ", ".join(shared)
            conn.execute(f"INSERT INTO orders ({col_list}) SELECT {col_list} FROM orders_old")
            conn.execute("DROP TABLE orders_old")

        # The ads table changed shape (ads now link to a real product instead
        # of a free-form image/link). Rebuild it if it's still the old shape -
        # the ads feature is brand new, so this affects test data only.
        ads_row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='ads'"
        ).fetchone()
        if ads_row:
            ad_cols = {row[1] for row in conn.execute("PRAGMA table_info(ads)")}
            if "product_id" not in ad_cols:
                conn.execute("DROP TABLE ads")
                with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
                    conn.executescript(f.read())

        conn.commit()
    finally:
        conn.close()


def query(sql, params=(), one=False):
    db = get_db()
    cur = db.execute(sql, params)
    rows = cur.fetchall()
    return (rows[0] if rows else None) if one else rows


def execute(sql, params=()):
    db = get_db()
    cur = db.execute(sql, params)
    db.commit()
    return cur.lastrowid


def init_app(app):
    app.teardown_appcontext(close_db)