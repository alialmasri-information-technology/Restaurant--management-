"""Shared test base: every test gets its own throwaway database."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from app import auth, config, db


class DatabaseTestCase(unittest.TestCase):
    """Points the data layer at a temporary SQLite file for the duration."""

    seed_demo = False

    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="re4-test-"))
        self._original_receipts = config.RECEIPTS_DIR
        config.RECEIPTS_DIR = self._tmp / "receipts"
        db.set_database_path(self._tmp / "test.db")
        db.init_db(seed_demo=self.seed_demo)
        self.admin = auth.authenticate("admin", "admin123")

    def tearDown(self) -> None:
        db.close_connection()
        db.set_database_path(None)
        config.RECEIPTS_DIR = self._original_receipts
        shutil.rmtree(self._tmp, ignore_errors=True)
