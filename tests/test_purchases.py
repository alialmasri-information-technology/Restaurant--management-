"""Purchase orders, goods receipt and weighted-average costing."""

from __future__ import annotations

import unittest

from app import config
from app.services import products as products_service
from app.services import purchases as service
from app.services import suppliers as suppliers_service
from tests.support import DatabaseTestCase


class PurchaseTestCase(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        self.supplier_id = suppliers_service.create_supplier(name="Levant Wholesale")
        self.product_id = products_service.create_product(
            sku="S1", name="Widget", price_usd="10.00", cost_usd="4.00",
            stock_qty=10, reorder_level=5,
        )

    def make_po(self, qty=10, unit_cost="6.00", status=config.PO_ORDERED):
        return service.create_po(
            supplier_id=self.supplier_id, user_id=self.admin.user_id,
            lines=[(self.product_id, qty, unit_cost)], status=status,
        )

    def first_item(self, po_id):
        return service.po_items(po_id)[0]["po_item_id"]


class CreateTests(PurchaseTestCase):
    def test_the_order_totals_its_lines(self):
        po_id = self.make_po(qty=10, unit_cost="6.00")
        self.assertEqual(service.get_po(po_id)["total_cost_usd"], 60.0)

    def test_order_numbers_increment(self):
        first = service.get_po(self.make_po())["po_no"]
        second = service.get_po(self.make_po())["po_no"]
        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith("PO-"))

    def test_repeated_products_are_merged_into_one_line(self):
        po_id = service.create_po(
            supplier_id=self.supplier_id, user_id=self.admin.user_id,
            lines=[(self.product_id, 3, "6.00"), (self.product_id, 2, "6.00")],
        )
        items = service.po_items(po_id)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["qty_ordered"], 5)

    def test_an_empty_order_is_refused(self):
        with self.assertRaises(service.PurchaseError):
            service.create_po(
                supplier_id=self.supplier_id, user_id=self.admin.user_id, lines=[]
            )

    def test_a_zero_quantity_is_refused(self):
        with self.assertRaises(service.PurchaseError):
            service.create_po(
                supplier_id=self.supplier_id, user_id=self.admin.user_id,
                lines=[(self.product_id, 0, "6.00")],
            )

    def test_ordering_does_not_move_stock(self):
        self.make_po(qty=10)
        self.assertEqual(products_service.get_product(self.product_id)["stock_qty"], 10)


class ReceiveTests(PurchaseTestCase):
    def test_receiving_adds_stock_and_closes_the_order(self):
        po_id = self.make_po(qty=10, unit_cost="6.00")
        result = service.receive(po_id, self.admin.user_id, {self.first_item(po_id): 10})
        self.assertEqual(result["status"], config.PO_RECEIVED)
        self.assertEqual(result["units"], 10)
        self.assertEqual(products_service.get_product(self.product_id)["stock_qty"], 20)

    def test_cost_is_the_weighted_average_of_old_and_new_stock(self):
        # 10 units already held at 4.00, 10 more arriving at 6.00 → 5.00.
        po_id = self.make_po(qty=10, unit_cost="6.00")
        service.receive(po_id, self.admin.user_id, {self.first_item(po_id): 10})
        self.assertEqual(products_service.get_product(self.product_id)["cost_usd"], 5.0)

    def test_the_cost_can_be_left_alone(self):
        po_id = self.make_po(qty=10, unit_cost="6.00")
        service.receive(
            po_id, self.admin.user_id, {self.first_item(po_id): 10}, update_cost=False
        )
        self.assertEqual(products_service.get_product(self.product_id)["cost_usd"], 4.0)

    def test_a_short_delivery_leaves_the_order_partially_received(self):
        po_id = self.make_po(qty=10)
        result = service.receive(po_id, self.admin.user_id, {self.first_item(po_id): 4})
        self.assertEqual(result["status"], config.PO_PARTIAL)
        self.assertEqual(service.po_items(po_id)[0]["qty_received"], 4)

    def test_the_rest_can_arrive_later(self):
        po_id = self.make_po(qty=10)
        item_id = self.first_item(po_id)
        service.receive(po_id, self.admin.user_id, {item_id: 4})
        result = service.receive(po_id, self.admin.user_id, {item_id: 6})
        self.assertEqual(result["status"], config.PO_RECEIVED)
        self.assertEqual(products_service.get_product(self.product_id)["stock_qty"], 20)

    def test_over_receiving_is_refused(self):
        po_id = self.make_po(qty=5)
        with self.assertRaises(service.PurchaseError):
            service.receive(po_id, self.admin.user_id, {self.first_item(po_id): 6})

    def test_receiving_a_finished_order_is_refused(self):
        po_id = self.make_po(qty=2)
        item_id = self.first_item(po_id)
        service.receive(po_id, self.admin.user_id, {item_id: 2})
        with self.assertRaises(service.PurchaseError):
            service.receive(po_id, self.admin.user_id, {item_id: 1})

    def test_receiving_a_cancelled_order_is_refused(self):
        po_id = self.make_po(qty=2)
        service.set_status(po_id, config.PO_CANCELLED)
        with self.assertRaises(service.PurchaseError):
            service.receive(po_id, self.admin.user_id, {self.first_item(po_id): 1})

    def test_receipt_is_logged_against_the_product(self):
        po_id = self.make_po(qty=3)
        service.receive(po_id, self.admin.user_id, {self.first_item(po_id): 3})
        history = products_service.stock_history(self.product_id)
        self.assertTrue(any(row["reason"] == "Purchase" for row in history))


class ReorderTests(PurchaseTestCase):
    def test_products_at_or_below_their_level_are_suggested(self):
        products_service.adjust_stock(
            self.product_id, -6, reason="Adjustment", user_id=self.admin.user_id
        )  # 10 -> 4, below the level of 5
        suggested = service.suggested_reorder()
        self.assertEqual([row["sku"] for row in suggested], ["S1"])

    def test_well_stocked_products_are_not_suggested(self):
        self.assertEqual(service.suggested_reorder(), [])


if __name__ == "__main__":
    unittest.main()
