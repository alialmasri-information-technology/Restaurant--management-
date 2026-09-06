"""SQLite access layer: connection handling, schema, and migrations."""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

from app import config, logs

SCHEMA_VERSION = 6

#: How long a writer waits for a competing lock before giving up. Two tills on
#: one database, or a backup running while a sale commits, otherwise surface as
#: "database is locked" the instant they overlap.
BUSY_TIMEOUT_MS = 5_000

CONNECTION_PRAGMAS = (
    "PRAGMA foreign_keys = ON",
    "PRAGMA journal_mode = WAL",
    f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}",
    # WAL already fsyncs at checkpoints; NORMAL trades a crash-window of the
    # last transaction for an order-of-magnitude faster checkout.
    "PRAGMA synchronous = NORMAL",
    "PRAGMA temp_store = MEMORY",
)

_local = threading.local()
_db_path: Path | None = None


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT    NOT NULL,
    role          TEXT    NOT NULL CHECK (role IN ('Admin', 'Employee')),
    full_name     TEXT    NOT NULL DEFAULT '',
    is_active     INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at    TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS categories (
    category_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE COLLATE NOCASE,
    -- Tax percentage for products in this category, overriding the store-wide
    -- rate. NULL falls back to it; essentials are often taxed differently.
    tax_rate    REAL CHECK (tax_rate IS NULL OR tax_rate >= 0)
);

CREATE TABLE IF NOT EXISTS suppliers (
    supplier_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL COLLATE NOCASE,
    contact_name  TEXT NOT NULL DEFAULT '',
    phone         TEXT NOT NULL DEFAULT '',
    email         TEXT NOT NULL DEFAULT '',
    address       TEXT NOT NULL DEFAULT '',
    payment_terms TEXT NOT NULL DEFAULT '',
    notes         TEXT NOT NULL DEFAULT '',
    is_active     INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS products (
    product_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    sku           TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    name          TEXT    NOT NULL,
    description   TEXT    NOT NULL DEFAULT '',
    category_id   INTEGER REFERENCES categories (category_id) ON DELETE SET NULL,
    cost_usd      REAL    NOT NULL DEFAULT 0 CHECK (cost_usd >= 0),
    price_usd     REAL    NOT NULL CHECK (price_usd >= 0),
    stock_qty     INTEGER NOT NULL DEFAULT 0,
    reorder_level INTEGER NOT NULL DEFAULT 5 CHECK (reorder_level >= 0),
    is_active     INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at    TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at    TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS customers (
    customer_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    phone       TEXT NOT NULL DEFAULT '',
    email       TEXT NOT NULL DEFAULT '',
    address     TEXT NOT NULL DEFAULT '',
    notes       TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS shifts (
    shift_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    opened_by         INTEGER REFERENCES users (user_id) ON DELETE SET NULL,
    opened_at         TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    opening_float_usd REAL    NOT NULL DEFAULT 0 CHECK (opening_float_usd >= 0),
    exchange_rate     REAL    NOT NULL DEFAULT 0,
    closed_by         INTEGER REFERENCES users (user_id) ON DELETE SET NULL,
    closed_at         TEXT,
    counted_usd       REAL    NOT NULL DEFAULT 0,
    counted_lbp       REAL    NOT NULL DEFAULT 0,
    expected_usd      REAL    NOT NULL DEFAULT 0,
    variance_usd      REAL    NOT NULL DEFAULT 0,
    status            TEXT    NOT NULL DEFAULT 'Open' CHECK (status IN ('Open', 'Closed')),
    note              TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS cash_movements (
    movement_id INTEGER PRIMARY KEY AUTOINCREMENT,
    shift_id    INTEGER NOT NULL REFERENCES shifts (shift_id) ON DELETE CASCADE,
    user_id     INTEGER REFERENCES users (user_id) ON DELETE SET NULL,
    kind        TEXT    NOT NULL CHECK (kind IN ('In', 'Out')),
    amount_usd  REAL    NOT NULL CHECK (amount_usd > 0),
    reason      TEXT    NOT NULL DEFAULT '',
    at          TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS sales (
    sale_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_no     TEXT    NOT NULL UNIQUE,
    customer_id    INTEGER REFERENCES customers (customer_id) ON DELETE SET NULL,
    user_id        INTEGER REFERENCES users (user_id) ON DELETE SET NULL,
    sale_time      TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    subtotal_usd   REAL    NOT NULL CHECK (subtotal_usd >= 0),
    discount_usd   REAL    NOT NULL DEFAULT 0 CHECK (discount_usd >= 0),
    tax_usd        REAL    NOT NULL DEFAULT 0 CHECK (tax_usd >= 0),
    total_usd      REAL    NOT NULL CHECK (total_usd >= 0),
    exchange_rate  REAL    NOT NULL DEFAULT 0,
    payment_method TEXT    NOT NULL DEFAULT 'Cash',
    paid_currency  TEXT    NOT NULL DEFAULT 'USD' CHECK (paid_currency IN ('USD', 'LBP')),
    amount_paid    REAL    NOT NULL DEFAULT 0,
    change_usd     REAL    NOT NULL DEFAULT 0,
    status         TEXT    NOT NULL DEFAULT 'Completed'
                           CHECK (status IN ('Completed', 'Refunded')),
    note           TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS sale_items (
    sale_item_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    sale_id        INTEGER NOT NULL REFERENCES sales (sale_id) ON DELETE CASCADE,
    product_id     INTEGER REFERENCES products (product_id) ON DELETE SET NULL,
    sku_at_sale    TEXT    NOT NULL DEFAULT '',
    name_at_sale   TEXT    NOT NULL,
    qty            INTEGER NOT NULL CHECK (qty > 0),
    unit_price_usd REAL    NOT NULL CHECK (unit_price_usd >= 0),
    cost_usd       REAL    NOT NULL DEFAULT 0 CHECK (cost_usd >= 0),
    line_total_usd REAL    NOT NULL CHECK (line_total_usd >= 0)
);

CREATE TABLE IF NOT EXISTS returns (
    return_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    return_no     TEXT    NOT NULL UNIQUE,
    sale_id       INTEGER NOT NULL REFERENCES sales (sale_id) ON DELETE CASCADE,
    user_id       INTEGER REFERENCES users (user_id) ON DELETE SET NULL,
    shift_id      INTEGER REFERENCES shifts (shift_id) ON DELETE SET NULL,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    total_usd     REAL    NOT NULL CHECK (total_usd >= 0),
    exchange_rate REAL    NOT NULL DEFAULT 0,
    refund_method TEXT    NOT NULL DEFAULT 'Cash',
    restock       INTEGER NOT NULL DEFAULT 1 CHECK (restock IN (0, 1)),
    reason        TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS return_items (
    return_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
    return_id      INTEGER NOT NULL REFERENCES returns (return_id) ON DELETE CASCADE,
    sale_item_id   INTEGER REFERENCES sale_items (sale_item_id) ON DELETE SET NULL,
    product_id     INTEGER REFERENCES products (product_id) ON DELETE SET NULL,
    name_at_sale   TEXT    NOT NULL,
    qty            INTEGER NOT NULL CHECK (qty > 0),
    unit_price_usd REAL    NOT NULL CHECK (unit_price_usd >= 0),
    line_total_usd REAL    NOT NULL CHECK (line_total_usd >= 0)
);

CREATE TABLE IF NOT EXISTS parked_sales (
    parked_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    label      TEXT    NOT NULL,
    user_id    INTEGER REFERENCES users (user_id) ON DELETE SET NULL,
    created_at TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    payload    TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS purchase_orders (
    po_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    po_no         TEXT    NOT NULL UNIQUE,
    supplier_id   INTEGER REFERENCES suppliers (supplier_id) ON DELETE SET NULL,
    user_id       INTEGER REFERENCES users (user_id) ON DELETE SET NULL,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    expected_date TEXT    NOT NULL DEFAULT '',
    received_at   TEXT,
    status        TEXT    NOT NULL DEFAULT 'Draft'
                          CHECK (status IN ('Draft', 'Ordered', 'Partially Received',
                                            'Received', 'Cancelled')),
    total_cost_usd REAL   NOT NULL DEFAULT 0 CHECK (total_cost_usd >= 0),
    note          TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS purchase_order_items (
    po_item_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    po_id          INTEGER NOT NULL REFERENCES purchase_orders (po_id) ON DELETE CASCADE,
    product_id     INTEGER NOT NULL REFERENCES products (product_id) ON DELETE CASCADE,
    qty_ordered    INTEGER NOT NULL CHECK (qty_ordered > 0),
    qty_received   INTEGER NOT NULL DEFAULT 0 CHECK (qty_received >= 0),
    unit_cost_usd  REAL    NOT NULL CHECK (unit_cost_usd >= 0),
    line_total_usd REAL    NOT NULL CHECK (line_total_usd >= 0)
);

CREATE TABLE IF NOT EXISTS inventory_log (
    log_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id  INTEGER NOT NULL REFERENCES products (product_id) ON DELETE CASCADE,
    change_qty  INTEGER NOT NULL,
    new_stock   INTEGER NOT NULL,
    reason      TEXT    NOT NULL,
    sale_id     INTEGER REFERENCES sales (sale_id) ON DELETE SET NULL,
    user_id     INTEGER REFERENCES users (user_id) ON DELETE SET NULL,
    note        TEXT    NOT NULL DEFAULT '',
    log_time    TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS audit_log (
    audit_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id   INTEGER REFERENCES users (user_id) ON DELETE SET NULL,
    username  TEXT    NOT NULL DEFAULT '',
    action    TEXT    NOT NULL,
    entity    TEXT    NOT NULL DEFAULT '',
    entity_id TEXT    NOT NULL DEFAULT '',
    detail    TEXT    NOT NULL DEFAULT '',
    at        TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- What each customer owes, as a running ledger rather than a single balance
-- column. Every event that moves the debt writes one signed row: a credit sale
-- adds, a payment or a refund subtracts. The balance is the sum, which means it
-- can always be explained line by line — and a statement is just this table
-- filtered to one customer.
CREATE TABLE IF NOT EXISTS customer_ledger (
    entry_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers (customer_id) ON DELETE CASCADE,
    at          TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    kind        TEXT    NOT NULL CHECK (kind IN ('Sale', 'Payment', 'Refund', 'Adjustment')),
    -- Signed: positive increases what the customer owes, negative reduces it.
    amount_usd  REAL    NOT NULL,
    sale_id     INTEGER REFERENCES sales (sale_id) ON DELETE SET NULL,
    return_id   INTEGER REFERENCES returns (return_id) ON DELETE SET NULL,
    shift_id    INTEGER REFERENCES shifts (shift_id) ON DELETE SET NULL,
    user_id     INTEGER REFERENCES users (user_id) ON DELETE SET NULL,
    method      TEXT    NOT NULL DEFAULT '',
    reference   TEXT    NOT NULL DEFAULT '',
    note        TEXT    NOT NULL DEFAULT ''
);

-- One row per username that has recently failed to sign in. Cleared on a
-- successful sign-in, so a shop that never gets its password wrong stays empty.
CREATE TABLE IF NOT EXISTS login_throttle (
    username     TEXT PRIMARY KEY COLLATE NOCASE,
    fail_count   INTEGER NOT NULL DEFAULT 0,
    first_fail_at TEXT   NOT NULL DEFAULT (datetime('now', 'localtime')),
    last_fail_at TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    locked_until TEXT
);

-- A gift card: a small liability with a paper trail. The balance is whatever
-- was loaded less whatever has been spent, and every movement is one signed
-- event row, so a card's history can be read line by line like the customer
-- ledger can. Cards are sold at the till (the sale pays for them) and are
-- activated inside that sale's transaction.
CREATE TABLE IF NOT EXISTS gift_cards (
    card_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    code          TEXT    NOT NULL UNIQUE,
    balance_usd   REAL    NOT NULL DEFAULT 0 CHECK (balance_usd >= 0),
    initial_usd   REAL    NOT NULL DEFAULT 0 CHECK (initial_usd >= 0),
    status        TEXT    NOT NULL DEFAULT 'Active'
                           CHECK (status IN ('Active', 'Empty', 'Disabled')),
    sold_usd      REAL    NOT NULL DEFAULT 0,
    sold_sale_id  INTEGER REFERENCES sales (sale_id) ON DELETE SET NULL,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    note          TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS gift_card_events (
    event_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id    INTEGER NOT NULL REFERENCES gift_cards (card_id) ON DELETE CASCADE,
    kind       TEXT    NOT NULL CHECK (kind IN ('Issue', 'Sale', 'Redeem', 'Refund', 'Adjust')),
    -- Signed: positive loads the card, negative spends from it.
    amount_usd REAL    NOT NULL,
    sale_id    INTEGER REFERENCES sales (sale_id) ON DELETE SET NULL,
    return_id  INTEGER REFERENCES returns (return_id) ON DELETE SET NULL,
    user_id    INTEGER REFERENCES users (user_id) ON DELETE SET NULL,
    at         TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    note       TEXT    NOT NULL DEFAULT ''
);

-- A physical inventory count. Expected quantities are frozen when the count
-- opens, so trading during the count does not move the goalposts; the variance
-- is only posted to stock when the count is applied.
CREATE TABLE IF NOT EXISTS stock_takes (
    stock_take_id INTEGER PRIMARY KEY AUTOINCREMENT,
    reference     TEXT    NOT NULL UNIQUE,
    opened_by     INTEGER REFERENCES users (user_id) ON DELETE SET NULL,
    opened_at     TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    closed_by     INTEGER REFERENCES users (user_id) ON DELETE SET NULL,
    closed_at     TEXT,
    status        TEXT    NOT NULL DEFAULT 'Open'
                          CHECK (status IN ('Open', 'Applied', 'Cancelled')),
    scope         TEXT    NOT NULL DEFAULT 'All products',
    category_id   INTEGER REFERENCES categories (category_id) ON DELETE SET NULL,
    note          TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS stock_take_items (
    stock_take_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_take_id INTEGER NOT NULL REFERENCES stock_takes (stock_take_id) ON DELETE CASCADE,
    product_id    INTEGER NOT NULL REFERENCES products (product_id) ON DELETE CASCADE,
    sku_at_count  TEXT    NOT NULL DEFAULT '',
    name_at_count TEXT    NOT NULL DEFAULT '',
    expected_qty  INTEGER NOT NULL DEFAULT 0,
    counted_qty   INTEGER,
    cost_usd      REAL    NOT NULL DEFAULT 0,
    counted_at    TEXT,
    UNIQUE (stock_take_id, product_id)
);

CREATE INDEX IF NOT EXISTS idx_products_name     ON products (name);
CREATE INDEX IF NOT EXISTS idx_products_category ON products (category_id);
CREATE INDEX IF NOT EXISTS idx_sales_time        ON sales (sale_time);
CREATE INDEX IF NOT EXISTS idx_sales_customer    ON sales (customer_id);
CREATE INDEX IF NOT EXISTS idx_sale_items_sale   ON sale_items (sale_id);
CREATE INDEX IF NOT EXISTS idx_inventory_product ON inventory_log (product_id);
CREATE INDEX IF NOT EXISTS idx_returns_sale      ON returns (sale_id);
CREATE INDEX IF NOT EXISTS idx_return_items_ret  ON return_items (return_id);
CREATE INDEX IF NOT EXISTS idx_cash_shift        ON cash_movements (shift_id);
CREATE INDEX IF NOT EXISTS idx_po_items_po       ON purchase_order_items (po_id);
CREATE INDEX IF NOT EXISTS idx_audit_at          ON audit_log (at);
CREATE INDEX IF NOT EXISTS idx_take_items_take  ON stock_take_items (stock_take_id);
CREATE INDEX IF NOT EXISTS idx_ledger_customer  ON customer_ledger (customer_id);
CREATE INDEX IF NOT EXISTS idx_ledger_sale      ON customer_ledger (sale_id);
"""

# Columns added after v1. Applied idempotently so an existing shop database is
# upgraded in place rather than replaced.
ADDED_COLUMNS = (
    ("products", "barcode", "TEXT NOT NULL DEFAULT ''"),
    ("products", "image_path", "TEXT NOT NULL DEFAULT ''"),
    ("products", "supplier_id", "INTEGER REFERENCES suppliers (supplier_id)"),
    ("sales", "shift_id", "INTEGER REFERENCES shifts (shift_id)"),
    ("sale_items", "discount_usd", "REAL NOT NULL DEFAULT 0"),
    ("sale_items", "returned_qty", "INTEGER NOT NULL DEFAULT 0"),
    # v3
    ("users", "must_change_password", "INTEGER NOT NULL DEFAULT 0"),
    ("users", "last_login_at", "TEXT"),
    # v4
    ("customers", "credit_limit_usd", "REAL NOT NULL DEFAULT 0"),
    # v5
    ("categories", "tax_rate",
     "REAL CHECK (tax_rate IS NULL OR tax_rate >= 0)"),
)

# Every column the services filter or aggregate on that the schema above does
# not cover. Barcode lookup scans the shelf, X/Z reports sum the ledger per
# drawer, the CSV importer and the product-delete guard probe sale and purchase
# lines by product, the suppliers screen counts products per supplier, and the
# day report and returns list walk a day's ledger and returns by timestamp.
# Without these each of those is a full-table scan that grows for as long as
# the shop trades.
LATE_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_products_barcode ON products (barcode)",
    "CREATE INDEX IF NOT EXISTS idx_sales_shift      ON sales (shift_id)",
    "CREATE INDEX IF NOT EXISTS idx_ledger_shift     ON customer_ledger (shift_id)",
    "CREATE INDEX IF NOT EXISTS idx_ledger_at        ON customer_ledger (at)",
    "CREATE INDEX IF NOT EXISTS idx_sale_items_prod  ON sale_items (product_id)",
    "CREATE INDEX IF NOT EXISTS idx_po_items_prod    ON purchase_order_items (product_id)",
    "CREATE INDEX IF NOT EXISTS idx_products_sup     ON products (supplier_id)",
    "CREATE INDEX IF NOT EXISTS idx_returns_at       ON returns (created_at)",
    "CREATE INDEX IF NOT EXISTS idx_audit_action     ON audit_log (action)",
    "CREATE INDEX IF NOT EXISTS idx_card_events      ON gift_card_events (card_id)",
    "CREATE INDEX IF NOT EXISTS idx_card_events_sale ON gift_card_events (sale_id)",
)


def set_database_path(path) -> None:
    """Point the layer at a different file, or ``None`` to restore the default."""
    global _db_path
    close_connection()
    _db_path = Path(path) if path is not None else None


def database_path() -> Path:
    return _db_path if _db_path is not None else config.DATABASE_PATH


def get_connection() -> sqlite3.Connection:
    """One connection per thread, created on demand."""
    conn = getattr(_local, "conn", None)
    if conn is not None and getattr(_local, "path", None) == database_path():
        return conn

    close_connection()
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=BUSY_TIMEOUT_MS / 1000)
    conn.row_factory = sqlite3.Row
    for pragma in CONNECTION_PRAGMAS:
        try:
            conn.execute(pragma)
        except sqlite3.DatabaseError:  # pragma: no cover - exotic SQLite builds
            logs.warning("SQLite rejected %s", pragma)
    _local.conn = conn
    _local.path = path
    return conn


def close_connection() -> None:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
    _local.conn = None
    _local.path = None
    _local.depth = 0


@contextmanager
def transaction():
    """Run a unit of work, committing on success and rolling back on error.

    Reentrant: only the outermost block commits. Service functions call each
    other freely (the CSV importer creates categories and suppliers inside its
    own transaction), and without this an inner ``db.execute`` would commit the
    outer unit of work halfway through and defeat its rollback.
    """
    conn = get_connection()
    depth = getattr(_local, "depth", 0)
    _local.depth = depth + 1
    try:
        yield conn
    except Exception:
        if depth == 0:
            conn.rollback()
        raise
    else:
        if depth == 0:
            conn.commit()
    finally:
        _local.depth = depth


def query(sql: str, params=()) -> list[sqlite3.Row]:
    return get_connection().execute(sql, params).fetchall()


def query_one(sql: str, params=()) -> sqlite3.Row | None:
    return get_connection().execute(sql, params).fetchone()


def scalar(sql: str, params=(), default=None):
    row = query_one(sql, params)
    if row is None or row[0] is None:
        return default
    return row[0]


def execute(sql: str, params=()) -> int:
    """Run a single writing statement in its own transaction."""
    with transaction() as conn:
        cursor = conn.execute(sql, params)
        return cursor.lastrowid


def date_range_clauses(column: str, date_from: str | None, date_to: str | None):
    """WHERE clauses for an inclusive date range on a TEXT timestamp column.

    Written sargably: ``date({column}) >= date(?)`` wraps the column in a
    function, which forces SQLite to read and parse every row before it can
    compare. Comparing the raw ISO timestamp lexicographically
    (``'2026-09-06 17:20' >= '2026-09-06'``) lets the query use the index on
    that column instead, which is what keeps the day report, the ledger and the
    audit trail fast on a database a shop has been trading on for years.
    """
    clauses: list[str] = []
    params: list = []
    if date_from:
        clauses.append(f"{column} >= date(?)")
        params.append(date_from)
    if date_to:
        # Exclusive upper bound one day past the last inclusive date.
        clauses.append(f"{column} < date(?, '+1 day')")
        params.append(date_to)
    return clauses, params


def table_columns(table: str) -> set[str]:
    return {row["name"] for row in query(f"PRAGMA table_info({table})")}


def checkpoint() -> None:
    """Fold the write-ahead log back into the database file and shrink it.

    WAL grows between checkpoints, and on a till that is never shut down
    gracefully it grows for as long as the shop trades — leaving a power cut
    with a bigger backlog to recover, and a backup that must replay all of it.
    TRUNCATE resets the file so the next session begins small.
    """
    try:
        get_connection().execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.DatabaseError:  # pragma: no cover - exotic SQLite builds
        logs.warning("WAL checkpoint failed")


def foreign_key_problems() -> list[sqlite3.Row]:
    """Rows that point at something that is not there, if any exist."""
    return query("PRAGMA foreign_key_check")


def integrity_check() -> str:
    """Ask SQLite to verify the file. Returns 'ok' or a description of the damage."""
    rows = query("PRAGMA integrity_check")
    return "\n".join(str(row[0]) for row in rows) if rows else "ok"


def init_db(seed_demo: bool = False) -> None:
    """Create the schema if missing, apply migrations, and seed defaults."""
    config.ensure_directories()
    conn = get_connection()
    with transaction():
        conn.executescript(SCHEMA)
    _migrate(conn)
    _seed_settings()
    _seed_admin()
    if seed_demo:
        seed_demo_data()


def _migrate(conn: sqlite3.Connection) -> None:
    """Bring an older database up to ``SCHEMA_VERSION``.

    The CREATE TABLE IF NOT EXISTS statements above have already added any new
    tables, so migration only has to add columns to tables that already existed
    and stamp the version. Every step is idempotent, which means a half-applied
    upgrade (power cut mid-write) simply completes on the next start.
    """
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current > SCHEMA_VERSION:
        raise RuntimeError(
            f"Database at {database_path()} was written by a newer version of "
            f"{config.APP_NAME} (schema v{current}). Please update the application."
        )

    with transaction():
        for table, column, ddl in ADDED_COLUMNS:
            existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        for statement in LATE_INDEXES:
            conn.execute(statement)
        if current != SCHEMA_VERSION:
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def _seed_settings() -> None:
    with transaction() as conn:
        for key, value in config.DEFAULT_SETTINGS.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                (key, value),
            )


def _seed_admin() -> None:
    """Create the bootstrap admin only when there is no user at all."""
    from app import auth  # imported here to avoid a circular import at module load

    if scalar("SELECT COUNT(*) FROM users", default=0):
        return
    auth.create_user(
        username="admin",
        password="admin123",
        role=config.ROLE_ADMIN,
        full_name="Administrator",
        # The bootstrap password is printed in the README and shown on the login
        # screen, so it is public knowledge. Force a real one at first sign-in.
        must_change_password=True,
    )


def seed_demo_data() -> None:
    """Populate a few categories and products so the app is explorable."""
    if scalar("SELECT COUNT(*) FROM products", default=0):
        return

    demo_categories = ["Beverages", "Snacks", "Household", "Electronics"]
    demo_products = [
        ("BEV-001", "Bottled Water 500ml", "Beverages", 0.15, 0.50, 240, 40),
        ("BEV-002", "Cola Can 330ml", "Beverages", 0.40, 0.90, 180, 30),
        ("BEV-003", "Ground Coffee 250g", "Beverages", 3.20, 5.75, 40, 10),
        ("SNK-001", "Potato Chips 150g", "Snacks", 0.80, 1.60, 96, 20),
        ("SNK-002", "Chocolate Bar", "Snacks", 0.55, 1.25, 150, 25),
        ("HOU-001", "Dish Soap 1L", "Household", 1.10, 2.40, 60, 12),
        ("HOU-002", "Laundry Detergent 2kg", "Household", 4.50, 8.90, 24, 6),
        ("ELE-001", "USB-C Cable 1m", "Electronics", 1.80, 4.50, 35, 8),
        ("ELE-002", "AA Batteries (4 pack)", "Electronics", 1.20, 3.10, 50, 10),
        ("ELE-003", "Power Bank 10000mAh", "Electronics", 9.50, 19.90, 8, 5),
    ]
    demo_suppliers = [
        ("Levant Wholesale", "Nadia Khoury", "01 555 010", "sales@levantwholesale.example", "Net 30"),
        ("Beirut Distribution Co.", "Karim Aoun", "01 555 020", "orders@beirutdist.example", "Net 15"),
    ]

    with transaction() as conn:
        for name in demo_categories:
            conn.execute("INSERT OR IGNORE INTO categories (name) VALUES (?)", (name,))
        category_ids = {
            row["name"]: row["category_id"]
            for row in conn.execute("SELECT category_id, name FROM categories")
        }

        supplier_ids = []
        for name, contact, phone, email, terms in demo_suppliers:
            cursor = conn.execute(
                """
                INSERT INTO suppliers (name, contact_name, phone, email, payment_terms)
                VALUES (?, ?, ?, ?, ?)
                """,
                (name, contact, phone, email, terms),
            )
            supplier_ids.append(cursor.lastrowid)

        for index, (sku, name, category, cost, price, stock, reorder) in enumerate(demo_products):
            cursor = conn.execute(
                """
                INSERT INTO products
                    (sku, barcode, name, category_id, supplier_id, cost_usd, price_usd,
                     stock_qty, reorder_level)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sku, sku, name, category_ids.get(category),
                    supplier_ids[index % len(supplier_ids)],
                    cost, price, stock, reorder,
                ),
            )
            conn.execute(
                """
                INSERT INTO inventory_log (product_id, change_qty, new_stock, reason, note)
                VALUES (?, ?, ?, 'Initial', 'Demo data')
                """,
                (cursor.lastrowid, stock, stock),
            )

        conn.execute(
            """
            INSERT INTO customers (name, phone, email, address)
            VALUES ('Walk-in Account', '', '', '')
            """
        )
