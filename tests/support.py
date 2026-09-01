"""Shared test base: every test gets its own throwaway database."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from app import auth, config, db
from app.services import shifts as shifts_service


REDIRECTED_DIRS = ("RECEIPTS_DIR", "BACKUPS_DIR", "IMAGES_DIR", "LOGS_DIR")


class DatabaseTestCase(unittest.TestCase):
    """Points the data layer at a temporary SQLite file for the duration."""

    seed_demo = False

    #: Selling requires an open till, so by default every test gets one and
    #: exercises the same path the real app takes. Till tests opt out.
    opens_shift = True

    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="re4-test-"))
        # Every writable directory is redirected, so a test that takes a backup
        # or saves an image cannot leave anything in the real project folder.
        self._original_dirs = {name: getattr(config, name) for name in REDIRECTED_DIRS}
        for name in REDIRECTED_DIRS:
            setattr(config, name, self._tmp / name.split("_")[0].lower())
        db.set_database_path(self._tmp / "test.db")
        db.init_db(seed_demo=self.seed_demo)
        self.admin = auth.authenticate("admin", "admin123")
        self.shift_id = (
            shifts_service.open_shift(self.admin.user_id, opening_float=100)
            if self.opens_shift
            else None
        )

    def tearDown(self) -> None:
        db.close_connection()
        db.set_database_path(None)
        for name, original in self._original_dirs.items():
            setattr(config, name, original)
        shutil.rmtree(self._tmp, ignore_errors=True)
