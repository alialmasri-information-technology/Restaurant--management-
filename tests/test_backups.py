"""Database backup, pruning and restore."""

from __future__ import annotations

import shutil
import sqlite3
import time
import unittest
from unittest import mock

from app import db
from app.services import backups as service
from app.services import customers as customers_service
from app.services import products as products_service
from app.services import settings as settings_service
from tests.support import DatabaseTestCase


class CreateTests(DatabaseTestCase):
    def test_a_backup_is_a_readable_copy_of_the_data(self):
        products_service.create_product(sku="S1", name="Widget", price_usd="10.00")
        path = service.create("manual")

        conn = sqlite3.connect(str(path))
        try:
            names = [row[0] for row in conn.execute("SELECT name FROM products")]
        finally:
            conn.close()
        self.assertEqual(names, ["Widget"])

    def test_the_label_appears_in_the_filename(self):
        self.assertIn("before-stocktake", service.create("before stocktake").name)

    def test_backups_are_listed_newest_first(self):
        first = service.create("one")
        time.sleep(1.1)  # the filename stamp has one-second resolution
        second = service.create("two")
        listed = [entry["name"] for entry in service.list_backups()]
        self.assertEqual(listed, [second.name, first.name])
        self.assertIn(first.name, listed)

    def test_pruning_keeps_the_newest(self):
        for index in range(3):
            service.create(f"copy{index}")
            time.sleep(1.1)
        newest = service.list_backups()[0]["name"]
        self.assertEqual(service.prune(1), 2)
        self.assertEqual([e["name"] for e in service.list_backups()], [newest])

    def test_pruning_nothing_is_harmless(self):
        service.create("one")
        self.assertEqual(service.prune(0), 0)
        self.assertEqual(len(service.list_backups()), 1)

    def test_the_startup_backup_can_be_switched_off(self):
        settings_service.set_value("backup_on_start", "0")
        self.assertIsNone(service.run_startup_backup())
        self.assertEqual(service.list_backups(), [])

    def test_the_startup_backup_runs_and_prunes(self):
        settings_service.set_value("backup_on_start", "1")
        settings_service.set_value("backup_keep", "1")
        self.assertIsNotNone(service.run_startup_backup())
        self.assertEqual(len(service.list_backups()), 1)

    def test_the_shutdown_backup_is_off_by_default(self):
        service.create("startup copy")
        self.assertIsNone(service.run_shutdown_backup())
        self.assertEqual(len(service.list_backups()), 1)

    def test_the_shutdown_backup_runs_and_prunes(self):
        settings_service.set_value("backup_on_close", "1")
        settings_service.set_value("backup_keep", "1")
        self.assertIsNotNone(service.run_shutdown_backup())
        self.assertEqual(len(service.list_backups()), 1)


class RestoreTests(DatabaseTestCase):
    def test_restoring_brings_back_the_old_data(self):
        products_service.create_product(sku="S1", name="Widget", price_usd="10.00")
        snapshot = service.create("good")

        products_service.create_product(sku="S2", name="Mistake", price_usd="1.00")
        self.assertIsNotNone(products_service.get_by_sku("S2"))

        service.restore(snapshot)
        self.assertIsNotNone(products_service.get_by_sku("S1"))
        self.assertIsNone(products_service.get_by_sku("S2"))

    def test_a_safety_copy_of_the_current_data_is_kept(self):
        snapshot = service.create("good")
        customers_service.create_customer(name="About to disappear")
        safety = service.restore(snapshot)

        conn = sqlite3.connect(str(safety))
        try:
            names = [row[0] for row in conn.execute("SELECT name FROM customers")]
        finally:
            conn.close()
        self.assertIn("About to disappear", names)

    def test_the_database_still_works_after_a_restore(self):
        snapshot = service.create("good")
        service.restore(snapshot)
        product_id = products_service.create_product(
            sku="S9", name="After restore", price_usd="3.00"
        )
        self.assertEqual(products_service.get_product(product_id)["name"], "After restore")

    def test_a_restored_database_passes_its_integrity_check(self):
        snapshot = service.create("good")
        service.restore(snapshot)
        self.assertEqual(db.integrity_check(), "ok")

    def test_a_restore_can_be_undone_with_its_safety_copy(self):
        """The drill: restore, regret it, and get back exactly what was there."""
        snapshot = service.create("good")
        customers_service.create_customer(name="The thing the restore nearly lost")
        safety = service.restore(snapshot)
        # The restore took the shop back to before the customer existed.
        self.assertEqual(
            [row["name"] for row in db.query(
                "SELECT name FROM customers WHERE name LIKE 'The thing%'"
            )],
            [],
        )
        # Undo the restore with the safety copy.
        service.restore(safety)
        self.assertEqual(
            [row["name"] for row in db.query(
                "SELECT name FROM customers WHERE name LIKE 'The thing%'"
            )],
            ["The thing the restore nearly lost"],
        )

    def test_a_missing_file_is_refused(self):
        with self.assertRaises(service.BackupError):
            service.restore(self._tmp / "nope.db")

    def test_a_file_that_is_not_a_database_is_refused(self):
        junk = self._tmp / "junk.db"
        junk.write_text("this is not a database", encoding="utf-8")
        with self.assertRaises(service.BackupError):
            service.restore(junk)

    def test_someone_elses_database_is_refused(self):
        stranger = self._tmp / "stranger.db"
        conn = sqlite3.connect(str(stranger))
        try:
            conn.execute("CREATE TABLE unrelated (id INTEGER)")
            conn.commit()
        finally:
            conn.close()
        with self.assertRaises(service.BackupError) as caught:
            service.restore(stranger)
        self.assertIn("does not look like", str(caught.exception))

    def test_a_newer_schema_is_refused(self):
        future = service.create("future")
        conn = sqlite3.connect(str(future))
        try:
            conn.execute(f"PRAGMA user_version = {db.SCHEMA_VERSION + 1}")
            conn.commit()
        finally:
            conn.close()
        with self.assertRaises(service.BackupError) as caught:
            service.restore(future)
        self.assertIn("newer version", str(caught.exception))

    def test_a_refused_restore_leaves_the_live_data_alone(self):
        products_service.create_product(sku="S1", name="Widget", price_usd="10.00")
        junk = self._tmp / "junk.db"
        junk.write_text("not a database", encoding="utf-8")
        with self.assertRaises(service.BackupError):
            service.restore(junk)
        self.assertIsNotNone(products_service.get_by_sku("S1"))


class CheckpointTests(DatabaseTestCase):
    def test_checkpointing_shrinks_the_write_ahead_log(self):
        products_service.create_product(sku="S1", name="Widget", price_usd="10.00")
        db.checkpoint()
        wal = self._tmp / "test.db-wal"
        self.assertFalse(wal.exists() and wal.stat().st_size > 0)

    def test_foreign_key_problems_are_none_on_a_healthy_database(self):
        self.assertEqual(db.foreign_key_problems(), [])


class InterruptedRestoreTests(DatabaseTestCase):
    """A restore that fails must not be how the shop loses everything.

    The copy used to go straight over the live database. copyfile opens its
    destination for writing, which empties it before the first byte of the
    backup arrives, so a copy that then failed -- a disk filling up, a backup
    drive pulled out part way -- left no database at all. The message said
    "Could not restore the backup", which reads as though nothing happened.
    """

    def disk_fills_up(self):
        """Exactly what a full disk does: truncate, then fail to write."""
        def copyfile(src, dst, **kwargs):
            with open(dst, "wb") as handle:
                handle.write(b"")
            raise OSError(28, "No space left on device")

        return mock.patch.object(service.shutil, "copyfile", copyfile)

    def test_the_live_database_survives_a_copy_that_fails(self):
        products_service.create_product(sku="S1", name="Widget", price_usd="10.00")
        good = service.create("good")

        with self.disk_fills_up(), self.assertRaises(service.BackupError):
            service.restore(good)

        self.assertIsNotNone(
            products_service.get_by_sku("S1"),
            "the shop's data was destroyed by a restore that did not happen",
        )

    def test_the_failure_says_where_the_safety_copy_is(self):
        service.create("good")
        good = service.create("good")
        with self.disk_fills_up(), self.assertRaises(service.BackupError) as caught:
            service.restore(good)
        message = str(caught.exception)
        self.assertIn("left as it was", message)
        self.assertIn("before-restore", message)

    def test_no_half_written_file_is_left_behind(self):
        good = service.create("good")
        with self.disk_fills_up(), self.assertRaises(service.BackupError):
            service.restore(good)
        leftovers = [p.name for p in self._tmp.iterdir() if "restoring" in p.name]
        self.assertEqual(leftovers, [])

    def test_a_restore_still_works_after_all_that(self):
        """The guard must not have cost the feature."""
        products_service.create_product(sku="S1", name="Widget", price_usd="10.00")
        good = service.create("good")
        products_service.create_product(sku="S2", name="Later", price_usd="1.00")

        service.restore(good)

        self.assertIsNotNone(products_service.get_by_sku("S1"))
        self.assertIsNone(products_service.get_by_sku("S2"))


class AwkwardPathTests(DatabaseTestCase):
    """A folder name is not a URI, and was being pasted into one."""

    def test_a_backup_under_a_folder_with_a_hash_is_still_readable(self):
        """"file:C:/shop#1/re4.db" stops at the "#".

        SQLite was handed a path that ended before the file, so it opened a
        different and empty database -- and the shop was told a perfectly good
        backup was missing users, products and sales, which is the most
        alarming way to be wrong about it.
        """
        good = service.create("good")
        awkward = self._tmp / "shop#1"
        awkward.mkdir()
        moved = awkward / good.name
        shutil.copyfile(good, moved)

        service._verify_restorable(moved)  # must not raise

    def test_such_a_backup_can_actually_be_restored(self):
        products_service.create_product(sku="S1", name="Widget", price_usd="10.00")
        good = service.create("good")
        awkward = self._tmp / "shop#1"
        awkward.mkdir()
        moved = awkward / good.name
        shutil.copyfile(good, moved)
        products_service.create_product(sku="S2", name="Later", price_usd="1.00")

        service.restore(moved)

        self.assertIsNotNone(products_service.get_by_sku("S1"))
        self.assertIsNone(products_service.get_by_sku("S2"))

    def test_reading_one_does_not_create_a_stray_file(self):
        """The truncated URI had no mode=ro left on it, so SQLite made one."""
        good = service.create("good")
        awkward = self._tmp / "shop#1"
        awkward.mkdir()
        shutil.copyfile(good, awkward / good.name)
        before = sorted(p.name for p in self._tmp.iterdir())

        service._verify_restorable(awkward / good.name)

        self.assertEqual(sorted(p.name for p in self._tmp.iterdir()), before)


if __name__ == "__main__":
    unittest.main()
