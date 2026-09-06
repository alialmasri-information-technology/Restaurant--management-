"""Housekeeping removes only what has outlived its usefulness.

A shop's records must survive a careless number: the defaults keep everything
for ever, and the tests here check both that nothing is deleted when the
settings say keep, and that the right rows and files go when they do not.
"""

from __future__ import annotations

import datetime as dt

from app import config, db
from app.services import housekeeping
from app.services import settings as settings_service
from tests.support import DatabaseTestCase


def _days_ago(days: int) -> str:
    moment = dt.datetime.now() - dt.timedelta(days=days)
    return moment.isoformat(sep=" ", timespec="seconds")


class ThrottleHousekeepingTests(DatabaseTestCase):
    opens_shift = False

    def _throttle(self, username, *, days_ago=60, locked_until=None):
        with db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO login_throttle
                    (username, fail_count, first_fail_at, last_fail_at, locked_until)
                VALUES (?, 3, ?, ?, ?)
                """,
                (username, _days_ago(days_ago), _days_ago(days_ago), locked_until),
            )

    def _rows(self):
        return db.query("SELECT username FROM login_throttle")

    def test_stale_failures_are_removed(self):
        self._throttle("ghost", days_ago=60)
        removed = housekeeping.tidy()
        self.assertEqual(removed["throttle"], 1)
        self.assertEqual(len(self._rows()), 0)

    def test_recent_failures_are_kept(self):
        self._throttle("recent", days_ago=2)
        housekeeping.tidy()
        self.assertEqual([row["username"] for row in self._rows()], ["recent"])

    def test_an_active_lockout_is_never_removed(self):
        soon = (dt.datetime.now() + dt.timedelta(minutes=5)).isoformat(
            sep=" ", timespec="seconds"
        )
        self._throttle("locked", days_ago=60, locked_until=soon)
        housekeeping.tidy()
        self.assertEqual([row["username"] for row in self._rows()], ["locked"])


class ParkedSaleHousekeepingTests(DatabaseTestCase):
    def _parked(self, *, days_ago=60):
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO parked_sales (label, created_at, payload) VALUES (?, ?, '[]')",
                (f"held {_days_ago(days_ago)}", _days_ago(days_ago)),
            )

    def test_old_parked_sales_are_removed_under_the_default(self):
        self._parked(days_ago=40)
        removed = housekeeping.tidy()
        self.assertEqual(removed["parked"], 1)
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM parked_sales", default=0), 0)

    def test_recent_parked_sales_are_kept(self):
        self._parked(days_ago=2)
        housekeeping.tidy()
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM parked_sales", default=0), 1)

    def test_zero_keeps_everything(self):
        settings_service.set_value("parked_keep_days", "0")
        self._parked(days_ago=400)
        housekeeping.tidy()
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM parked_sales", default=0), 1)


class AuditHousekeepingTests(DatabaseTestCase):
    opens_shift = False

    def _audit_line(self, *, days_ago=60):
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO audit_log (username, action, at) VALUES ('system', 'Test', ?)",
                (_days_ago(days_ago),),
            )

    def _test_rows(self):
        return db.query("SELECT audit_id FROM audit_log WHERE action = 'Test'")

    def test_the_audit_log_is_kept_by_default(self):
        self._audit_line(days_ago=400)
        removed = housekeeping.tidy()
        self.assertEqual(removed["audit"], 0)
        self.assertEqual(len(self._test_rows()), 1)

    def test_a_stated_retention_is_honoured(self):
        settings_service.set_value("audit_keep_days", "30")
        self._audit_line(days_ago=60)
        self._audit_line(days_ago=2)
        removed = housekeeping.tidy()
        self.assertEqual(removed["audit"], 1)
        self.assertEqual(len(self._test_rows()), 1)


class ReceiptHousekeepingTests(DatabaseTestCase):
    opens_shift = False

    def _receipt(self, name, *, days_ago=60):
        path = config.RECEIPTS_DIR / name
        path.write_text("receipt")
        stamp = (dt.datetime.now() - dt.timedelta(days=days_ago)).timestamp()
        import os

        os.utime(path, (stamp, stamp))
        return path

    def test_receipts_are_kept_by_default(self):
        old = self._receipt("old.pdf", days_ago=400)
        housekeeping.tidy()
        self.assertTrue(old.exists())

    def test_a_stated_retention_is_honoured(self):
        settings_service.set_value("receipt_keep_days", "30")
        old = self._receipt("old.pdf", days_ago=60)
        new = self._receipt("new.pdf", days_ago=2)
        housekeeping.tidy()
        self.assertFalse(old.exists())
        self.assertTrue(new.exists())
