"""Shared test base: every test gets its own throwaway database."""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from app import auth, config, db
from app.services import shifts as shifts_service
from app.services import updates as updates_service

REDIRECTED_DIRS = ("RECEIPTS_DIR", "BACKUPS_DIR", "IMAGES_DIR", "LOGS_DIR")

# The test process must not phone home. A screen built in a test fires a real
# update check on the background worker, and that thread lands whenever it
# lands — often inside the *next* test, writing its cache rows into that
# test's database. The switch makes the check a no-op for the whole process;
# the update-check tests switch it back off around themselves deliberately.
os.environ.setdefault(updates_service.TEST_SWITCH, "1")

#: PBKDF2 rounds while testing. The production figure is deliberately expensive,
#: which is the right trade for one sign-in a day and the wrong one for a suite
#: that hashes hundreds of times — it costs minutes and proves nothing that a
#: cheap work factor does not. Anything above the 1,000 rounds the hash-upgrade
#: tests treat as "legacy" works.
TEST_ITERATIONS = 10_000


class DatabaseTestCase(unittest.TestCase):
    """Points the data layer at a temporary SQLite file for the duration."""

    seed_demo = False

    #: Selling requires an open till, so by default every test gets one and
    #: exercises the same path the real app takes. Till tests opt out.
    opens_shift = True

    def setUp(self) -> None:
        self._original_iterations = auth.ITERATIONS
        auth.ITERATIONS = TEST_ITERATIONS
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
        auth.ITERATIONS = self._original_iterations
        db.close_connection()
        db.set_database_path(None)
        for name, original in self._original_dirs.items():
            setattr(config, name, original)
        shutil.rmtree(self._tmp, ignore_errors=True)


def destroy_tk_root(root) -> None:
    """Destroy a Tk root without the usual burst of Tcl background errors.

    CustomTkinter schedules deferred work with ``after``: a titlebar icon at
    200ms, scaled min/max sizes at 1000ms, focus and grab fixes in between.
    Every window a test opens queues its own set, and a test is finished long
    before the slowest of them comes due. ``destroy`` deletes the Tcl command
    behind each callback but leaves the timer running, so Tcl reaches the
    appointed moment, finds the command gone, and prints "invalid command
    name ..." to stderr for every one. They are not failures and the run stays
    green, which is exactly the problem: real Tk errors would arrive looking
    identical and scroll past unread.

    Flushing first lets any queued idle work finish, cancelling then clears
    what is still outstanding across the whole interpreter — every window, not
    just this root — and only then is it safe to destroy.
    """
    try:
        root.update_idletasks()
        for job in root.tk.call("after", "info"):
            try:
                root.after_cancel(job)
            except Exception:  # noqa: BLE001 - fired between listing and cancelling
                pass
        root.destroy()
    except Exception:  # noqa: BLE001 - already gone
        pass
