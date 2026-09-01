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



class AuthAuditTests(DatabaseTestCase):
    """Account changes and sign-ins are the entries an owner actually reads."""

    def test_a_successful_sign_in_is_recorded(self):
        from app import auth

        auth.authenticate("admin", "admin123")
        entry = service.list_entries(action="Signed in")[0]
        self.assertEqual(entry["username"], "admin")

    def test_a_failed_sign_in_is_recorded_with_the_attempted_name(self):
        from app import auth

        with self.assertRaises(auth.AuthError):
            auth.authenticate("admin", "wrong")
        entry = service.list_entries(action="Sign-in failed")[0]
        self.assertEqual(entry["detail"], "admin")

    def test_a_deactivated_account_is_recorded_separately(self):
        from app import auth

        user_id = auth.create_user("cashier", "secret1", "Employee")
        auth.set_active(user_id, False)
        with self.assertRaises(auth.AuthError):
            auth.authenticate("cashier", "secret1")
        self.assertEqual(len(service.list_entries(action="Sign-in refused")), 1)

    def test_creating_and_editing_a_user_is_recorded(self):
        from app import auth

        service.set_actor(self.admin)
        self.addCleanup(service.clear_actor)
        user_id = auth.create_user("cashier", "secret1", "Employee")
        auth.update_user(user_id, username="cashier", role="Admin", full_name="Rami")

        actions = [entry["action"] for entry in service.list_entries()]
        self.assertIn("User created", actions)
        self.assertIn("User updated", actions)
        self.assertIn("role", service.list_entries(action="User updated")[0]["detail"])

    def test_a_password_change_is_recorded_without_the_password(self):
        from app import auth

        user_id = auth.create_user("cashier", "secret1", "Employee")
        auth.set_password(user_id, "brandnew1")
        entry = service.list_entries(action="Password changed")[0]
        self.assertNotIn("brandnew1", entry["detail"])

    def test_deactivating_a_user_is_recorded(self):
        from app import auth

        user_id = auth.create_user("cashier", "secret1", "Employee")
        auth.delete_user(user_id)
        self.assertEqual(len(service.list_entries(action="User deactivated")), 1)


if __name__ == "__main__":
    unittest.main()
