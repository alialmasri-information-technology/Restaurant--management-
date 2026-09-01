"""The audit trail."""

from __future__ import annotations

import logging
import unittest

from app.services import audit as service
from app.services import products as products_service
from tests.support import DatabaseTestCase


class AuditTests(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        service.set_actor(self.admin)
        self.addCleanup(service.clear_actor)

    def test_an_entry_records_the_signed_in_user(self):
        service.record("Test action", "thing", 1, "some detail")
        entry = service.list_entries()[0]
        self.assertEqual(entry["username"], "admin")
        self.assertEqual(entry["action"], "Test action")
        self.assertEqual(entry["detail"], "some detail")

    def test_actions_are_recorded_without_a_signed_in_user(self):
        service.clear_actor()
        service.record("Automatic action")
        self.assertEqual(service.list_entries()[0]["username"], "system")

    def test_creating_a_product_is_audited(self):
        products_service.create_product(sku="S1", name="Widget", price_usd="10.00")
        actions = [entry["action"] for entry in service.list_entries()]
        self.assertIn("Product created", actions)

    def test_an_edit_records_what_changed(self):
        product_id = products_service.create_product(
            sku="S1", name="Widget", price_usd="10.00", cost_usd="4.00"
        )
        products_service.update_product(
            product_id, sku="S1", name="Widget", price_usd="12.00", cost_usd="4.00"
        )
        entry = service.list_entries(action="Product updated")[0]
        self.assertIn("price_usd", entry["detail"])
        self.assertIn("12.0", entry["detail"])

    def test_entries_are_newest_first(self):
        service.record("First")
        service.record("Second")
        self.assertEqual(service.list_entries()[0]["action"], "Second")

    def test_entries_can_be_filtered_and_searched(self):
        service.record("Alpha", "thing", 1, "needle in here")
        service.record("Beta", "thing", 2, "nothing")
        self.assertEqual(len(service.list_entries(action="Alpha")), 1)
        self.assertEqual(len(service.list_entries(search="needle")), 1)
        self.assertEqual(len(service.list_entries(search="absent")), 0)

    def test_known_actions_are_listed_for_the_filter(self):
        service.record("Alpha")
        service.record("Alpha")
        service.record("Beta")
        self.assertIn("Alpha", service.known_actions())
        self.assertEqual(len(set(service.known_actions())), len(service.known_actions()))

    def test_describe_changes_only_mentions_what_moved(self):
        detail = service.describe_changes(
            {"price": 4.5, "name": "Widget"},
            {"price": 5.0, "name": "Widget"},
            ["price", "name"],
        )
        self.assertIn("price", detail)
        self.assertNotIn("name", detail)

    def test_a_failed_audit_write_does_not_break_the_caller(self):
        # The table is gone, but recording must still return quietly. The
        # failure is logged, so logging is muted to keep the test output clean.
        from app import db
        db.execute("DROP TABLE audit_log")
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        service.record("After the table vanished")


if __name__ == "__main__":
    unittest.main()
