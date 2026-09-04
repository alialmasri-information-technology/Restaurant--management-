"""Upgrading an existing shop database rather than replacing it.

The thing that must never happen is a shop opening after an update to find its
sales gone. These tests stand up a database shaped the way the previous release
left it, run the current start-up path over it, and check that the new columns
and tables arrived while every existing row stayed exactly where it was.
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app import auth, config, db

# The users table as schema v2 wrote it: no must_change_password, no
# last_login_at, and neither login_throttle nor the stock take tables alongside.
V2_USERS = """
CREATE TABLE users (
    user_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT    NOT NULL,
    role          TEXT    NOT NULL CHECK (role IN ('Admin', 'Employee')),
    full_name     TEXT    NOT NULL DEFAULT '',
    is_active     INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at    TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);
"""

V2_PRODUCTS = """
CREATE TABLE products (
    product_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    sku           TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    name          TEXT    NOT NULL,
    description   TEXT    NOT NULL DEFAULT '',
    category_id   INTEGER,
    cost_usd      REAL    NOT NULL DEFAULT 0,
    price_usd     REAL    NOT NULL,
    stock_qty     INTEGER NOT NULL DEFAULT 0,
    reorder_level INTEGER NOT NULL DEFAULT 5,
    is_active     INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at    TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    barcode       TEXT    NOT NULL DEFAULT '',
    image_path    TEXT    NOT NULL DEFAULT '',
    supplier_id   INTEGER
);
"""


class MigrationTests(unittest.TestCase):
    """Runs against a hand-built v2 file rather than the current schema."""

    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="re4-migrate-"))
        self._original_dirs = {
            name: getattr(config, name)
            for name in ("RECEIPTS_DIR", "BACKUPS_DIR", "IMAGES_DIR", "LOGS_DIR")
        }
        for name in self._original_dirs:
            setattr(config, name, self._tmp / name.split("_")[0].lower())
        self.path = self._tmp / "legacy.db"
        self._original_iterations = auth.ITERATIONS
        auth.ITERATIONS = 10_000

    def tearDown(self) -> None:
        auth.ITERATIONS = self._original_iterations
        db.close_connection()
        db.set_database_path(None)
        for name, original in self._original_dirs.items():
            setattr(config, name, original)
        shutil.rmtree(self._tmp, ignore_errors=True)

    def build_v2(self) -> None:
        conn = sqlite3.connect(str(self.path))
        conn.executescript(V2_USERS + V2_PRODUCTS)
        conn.execute(
            "INSERT INTO users (username, password_hash, role, full_name) "
            "VALUES ('owner', ?, 'Admin', 'The Owner')",
            (auth.hash_password("owner-password"),),
        )
        conn.execute(
            "INSERT INTO products (sku, name, price_usd, cost_usd, stock_qty) "
            "VALUES ('OLD-1', 'Legacy Widget', 9.5, 4.0, 17)"
        )
        conn.execute("PRAGMA user_version = 2")
        conn.commit()
        conn.close()

    def migrate(self) -> None:
        db.set_database_path(self.path)
        db.init_db()

    # -- the upgrade itself ------------------------------------------------ #

    def test_the_version_is_stamped_forward(self):
        self.build_v2()
        self.migrate()
        self.assertEqual(
            db.scalar("PRAGMA user_version", default=0), db.SCHEMA_VERSION
        )

    def test_the_new_columns_are_added_to_an_existing_users_table(self):
        self.build_v2()
        self.migrate()
        columns = db.table_columns("users")
        self.assertIn("must_change_password", columns)
        self.assertIn("last_login_at", columns)

    def test_the_new_tables_are_created(self):
        self.build_v2()
        self.migrate()
        names = {
            row["name"]
            for row in db.query("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        self.assertIn("login_throttle", names)
        self.assertIn("stock_takes", names)
        self.assertIn("stock_take_items", names)

    # -- what was already there -------------------------------------------- #

    def test_existing_rows_survive(self):
        self.build_v2()
        self.migrate()
        product = db.query_one("SELECT * FROM products WHERE sku = 'OLD-1'")
        self.assertEqual(product["name"], "Legacy Widget")
        self.assertEqual(product["stock_qty"], 17)

    def test_an_existing_account_still_signs_in(self):
        self.build_v2()
        self.migrate()
        user = auth.authenticate("owner", "owner-password")
        self.assertEqual(user.role, config.ROLE_ADMIN)
        # It was not created by this release, so it is not forced to re-choose.
        self.assertFalse(user.must_change_password)

    def test_no_bootstrap_admin_is_added_over_an_existing_user(self):
        self.build_v2()
        self.migrate()
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM users", default=0), 1)

    # -- running it twice --------------------------------------------------- #

    def test_migrating_again_changes_nothing(self):
        self.build_v2()
        self.migrate()
        db.close_connection()
        self.migrate()
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM users", default=0), 1)
        self.assertEqual(
            db.scalar("PRAGMA user_version", default=0), db.SCHEMA_VERSION
        )
        self.assertEqual(db.integrity_check(), "ok")

    def test_a_database_from_a_newer_release_is_refused(self):
        """Better to stop than to write v3 rows into a v4 file."""
        self.build_v2()
        conn = sqlite3.connect(str(self.path))
        conn.execute(f"PRAGMA user_version = {db.SCHEMA_VERSION + 1}")
        conn.commit()
        conn.close()

        db.set_database_path(self.path)
        with self.assertRaises(RuntimeError) as caught:
            db.init_db()
        self.assertIn("newer version", str(caught.exception))


class FreshDatabaseTests(unittest.TestCase):
    """A brand new file arrives at the same place a migrated one does."""

    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="re4-fresh-"))
        self._original_dirs = {
            name: getattr(config, name)
            for name in ("RECEIPTS_DIR", "BACKUPS_DIR", "IMAGES_DIR", "LOGS_DIR")
        }
        for name in self._original_dirs:
            setattr(config, name, self._tmp / name.split("_")[0].lower())
        self._original_iterations = auth.ITERATIONS
        auth.ITERATIONS = 10_000
        db.set_database_path(self._tmp / "fresh.db")
        db.init_db()

    def tearDown(self) -> None:
        auth.ITERATIONS = self._original_iterations
        db.close_connection()
        db.set_database_path(None)
        for name, original in self._original_dirs.items():
            setattr(config, name, original)
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_the_write_ahead_log_and_a_busy_timeout_are_in_force(self):
        self.assertEqual(
            str(db.scalar("PRAGMA journal_mode", default="")).lower(), "wal"
        )
        self.assertEqual(
            db.scalar("PRAGMA busy_timeout", default=0), db.BUSY_TIMEOUT_MS
        )
        self.assertEqual(db.scalar("PRAGMA foreign_keys", default=0), 1)

    def test_every_default_setting_is_seeded(self):
        stored = {row["key"] for row in db.query("SELECT key FROM settings")}
        self.assertEqual(stored, set(config.DEFAULT_SETTINGS))
