"""Catalogue and stock movements."""

from __future__ import annotations

import unittest

from app import db
from app.services import products as service
from tests.support import DatabaseTestCase


class ProductTests(DatabaseTestCase):
    def make(self, **overrides):
        values = {
            "sku": "SKU-1", "name": "Widget", "price_usd": "4.50",
            "cost_usd": "2.00", "stock_qty": 10, "reorder_level": 3,
            "user_id": self.admin.user_id,
        }
        values.update(overrides)
        return service.create_product(**values)

    def test_create_and_read_back(self):
        product_id = self.make()
        product = service.get_product(product_id)
        self.assertEqual(product["name"], "Widget")
        self.assertEqual(product["price_usd"], 4.50)
        self.assertEqual(product["stock_qty"], 10)

    def test_opening_stock_is_logged(self):
        product_id = self.make()
        history = service.stock_history(product_id)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["reason"], "Initial")
        self.assertEqual(history[0]["change_qty"], 10)

    def test_duplicate_sku_is_refused(self):
        self.make()
        with self.assertRaises(service.ProductError):
            self.make(name="Other")

    def test_blank_name_is_refused(self):
        with self.assertRaises(service.ProductError):
            self.make(name="   ")

    def test_negative_price_is_refused(self):
        with self.assertRaises(service.ProductError):
            self.make(price_usd=-1)

    def test_search_matches_name_and_sku(self):
        self.make(sku="ABC-1", name="Blue Mug")
        self.make(sku="XYZ-9", name="Red Plate")
        self.assertEqual(len(service.list_products(search="mug")), 1)
        self.assertEqual(len(service.list_products(search="XYZ")), 1)
        self.assertEqual(len(service.list_products(search="nothing")), 0)

    def test_archived_products_are_hidden_by_default(self):
        product_id = self.make()
        service.set_active(product_id, False)
        self.assertEqual(len(service.list_products()), 0)
        self.assertEqual(len(service.list_products(include_inactive=True)), 1)

    def test_a_long_catalogue_stops_at_the_cap_and_says_so(self):
        """A browsing list is bounded; it must also admit that it is."""
        for number in range(12):
            self.make(sku=f"CAP-{number}", name=f"Capped {number:02d}")

        rows = service.list_products(limit=5)
        self.assertEqual(len(rows), 5)
        self.assertTrue(rows.truncated)
        # The cap takes the first five in the list's own order, not five at random.
        self.assertEqual(
            [row["name"] for row in rows],
            [f"Capped {number:02d}" for number in range(5)],
        )

    def test_a_catalogue_inside_the_cap_is_not_marked_truncated(self):
        self.make(sku="ONE", name="Only one")
        rows = service.list_products(limit=5)
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows.truncated)

    def test_the_cap_can_be_lifted_for_exports_and_reorder_lists(self):
        for number in range(12):
            self.make(sku=f"ALL-{number}", name=f"Everything {number:02d}")
        rows = service.list_products(limit=None)
        self.assertEqual(len(rows), 12)
        self.assertFalse(rows.truncated)


class StockTests(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        self.product_id = service.create_product(
            sku="SKU-1", name="Widget", price_usd="4.50", cost_usd="2.00",
            stock_qty=10, reorder_level=3, user_id=self.admin.user_id,
        )

    def test_restock_increases_stock(self):
        new_stock = service.adjust_stock(self.product_id, 5, "Restock", self.admin.user_id)
        self.assertEqual(new_stock, 15)
        self.assertEqual(service.get_product(self.product_id)["stock_qty"], 15)

    def test_stock_cannot_go_negative(self):
        with self.assertRaises(service.ProductError):
            service.adjust_stock(self.product_id, -11, "Adjustment", self.admin.user_id)
        self.assertEqual(service.get_product(self.product_id)["stock_qty"], 10)

    def test_zero_change_is_refused(self):
        with self.assertRaises(service.ProductError):
            service.adjust_stock(self.product_id, 0)

    def test_every_movement_is_logged_with_a_balance(self):
        service.adjust_stock(self.product_id, 5, "Restock", self.admin.user_id)
        service.adjust_stock(self.product_id, -2, "Spoilage", self.admin.user_id)
        history = service.stock_history(self.product_id)
        self.assertEqual([row["new_stock"] for row in history], [13, 15, 10])

    def test_low_stock_uses_the_reorder_level(self):
        self.assertEqual(len(service.low_stock_products()), 0)
        service.adjust_stock(self.product_id, -7, "Adjustment", self.admin.user_id)
        low = service.low_stock_products()
        self.assertEqual(len(low), 1)
        self.assertEqual(low[0]["stock_qty"], 3)

    def test_unsold_product_is_deleted_outright(self):
        service.delete_product(self.product_id)
        self.assertIsNone(service.get_product(self.product_id))

    def test_a_counted_product_is_archived_rather_than_deleted(self):
        """stock_take_items cascades, so deleting one edits the count.

        A sheet that had found three units missing went back to reporting no
        variance at all, with nothing to say a line had ever been on it. A
        shortage that disappears when someone deletes the product is worse
        than not counting.
        """
        from app.services import stocktake

        take_id = stocktake.open_count(self.admin.user_id)
        stocktake.record_count(take_id, self.product_id, 7)
        before = stocktake.summarise(stocktake.list_items(take_id))
        self.assertEqual(before["shortage_units"], 3)

        with self.assertRaises(service.ProductError):
            service.delete_product(self.product_id)

        # Archived, not gone, and the sheet still says what it found.
        product = service.get_product(self.product_id)
        self.assertIsNotNone(product)
        self.assertEqual(product["is_active"], 0)
        after = stocktake.summarise(stocktake.list_items(take_id))
        self.assertEqual(after["shortage_units"], 3)
        self.assertEqual(after["line_count"], before["line_count"])
        self.assertEqual(stocktake.variance_value(take_id), -6)


class CategoryTests(DatabaseTestCase):
    def test_create_rename_delete(self):
        category_id = service.create_category("Drinks")
        service.rename_category(category_id, "Beverages")
        self.assertEqual(service.list_categories()[0]["name"], "Beverages")
        service.delete_category(category_id)
        self.assertEqual(len(service.list_categories()), 0)

    def test_duplicate_name_is_refused(self):
        service.create_category("Drinks")
        with self.assertRaises(service.ProductError):
            service.create_category("drinks")

    def test_deleting_a_category_keeps_its_products(self):
        category_id = service.create_category("Drinks")
        product_id = service.create_product(
            sku="S1", name="Cola", price_usd="1", category_id=category_id
        )
        service.delete_category(category_id)
        product = service.get_product(product_id)
        self.assertIsNotNone(product)
        self.assertIsNone(product["category_id"])


class MigrationTests(DatabaseTestCase):
    def test_init_db_is_idempotent(self):
        service.create_product(sku="S1", name="Cola", price_usd="1")
        db.init_db()
        self.assertEqual(len(service.list_products()), 1)

    def test_newer_schema_is_refused(self):
        db.get_connection().execute(f"PRAGMA user_version = {db.SCHEMA_VERSION + 5}")
        with self.assertRaises(RuntimeError):
            db.init_db()


if __name__ == "__main__":
    unittest.main()
