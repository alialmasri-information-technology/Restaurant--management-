"""Layaways: held honestly, collected as a sale, cancelled with the money back."""

from __future__ import annotations

import unittest
from decimal import Decimal

from app import db
from app.services import layaways as service
from app.services import products as products_service
from app.services import sales as sales_service
from app.services import shifts as shifts_service
from tests.support import DatabaseTestCase


class HoldTests(DatabaseTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.product_id = products_service.create_product(
            sku="S1", name="Widget", price_usd="10.00", stock_qty=5
        )

    def _cart(self, qty: int = 2) -> sales_service.Cart:
        cart = sales_service.Cart()
        cart.add_product(products_service.get_product(self.product_id), qty)
        return cart

    def test_holding_freezes_the_price_and_the_total(self):
        layaway_id = service.hold(self._cart(), self.admin.user_id, deposit="5.00")
        product = products_service.get_product(self.product_id)
        products_service.update_product(
            self.product_id, sku=product["sku"], name=product["name"],
            price_usd="99.00", cost_usd=product["cost_usd"],
        )
        layaway = service.get_layaway(layaway_id)
        self.assertEqual(layaway["total_usd"], 20.0)
        self.assertEqual(layaway["deposit_usd"], 5.0)
        self.assertEqual(service.outstanding(layaway_id), Decimal("15.00"))

    def test_a_cash_deposit_goes_into_the_drawer(self):
        before = shifts_service.totals(self.shift_id)["expected_usd"]
        service.hold(self._cart(), self.admin.user_id, deposit="5.00", deposit_method="Cash")
        after = shifts_service.totals(self.shift_id)["expected_usd"]
        self.assertEqual(after, before + 5.0)

    def test_a_card_deposit_leaves_the_drawer_alone(self):
        before = shifts_service.totals(self.shift_id)["expected_usd"]
        service.hold(self._cart(), self.admin.user_id, deposit="5.00", deposit_method="Card")
        after = shifts_service.totals(self.shift_id)["expected_usd"]
        self.assertEqual(after, before)

    def test_holding_does_not_touch_the_shelf(self):
        service.hold(self._cart(), self.admin.user_id)
        self.assertEqual(products_service.get_product(self.product_id)["stock_qty"], 5)

    def test_the_cart_cannot_be_empty(self):
        with self.assertRaises(service.LayawayError):
            service.hold(sales_service.Cart(), self.admin.user_id)

    def test_the_deposit_cannot_exceed_the_total(self):
        with self.assertRaises(service.LayawayError):
            service.hold(self._cart(), self.admin.user_id, deposit="99.00")

    def test_a_gift_card_cannot_go_on_layaway(self):
        from app.services import giftcards as giftcards_service

        cart = self._cart()
        cart.add_gift_card(giftcards_service.generate_code(), "10.00")
        with self.assertRaises(service.LayawayError):
            service.hold(cart, self.admin.user_id)


class CollectTests(DatabaseTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.product_id = products_service.create_product(
            sku="S1", name="Widget", price_usd="10.00", cost_usd="2.00", stock_qty=5
        )
        cart = sales_service.Cart()
        cart.add_product(products_service.get_product(self.product_id), 2)
        self.layaway_id = service.hold(cart, self.admin.user_id, deposit="5.00")

    def test_collection_completes_as_a_normal_sale(self):
        sale_id = service.collect(self.layaway_id, self.admin.user_id, amount_paid="15.00")
        sale = sales_service.get_sale(sale_id)
        self.assertEqual(sale["total_usd"], 20.0)
        # The invoice has received everything across both moments: the
        # deposit then, the remainder now.
        self.assertEqual(sale["amount_paid"], 20.0)
        self.assertEqual(sale["status"], "Completed")
        layaway = service.get_layaway(self.layaway_id)
        self.assertEqual(layaway["status"], "Collected")
        self.assertEqual(layaway["completed_sale_id"], sale_id)

    def test_collection_takes_its_stock_and_not_twice(self):
        sale_id = service.collect(self.layaway_id, self.admin.user_id, amount_paid="15.00")
        product = products_service.get_product(self.product_id)
        self.assertEqual(product["stock_qty"], 3)
        # The inventory log saw one movement, not two.
        moves = db.query(
            "SELECT change_qty FROM inventory_log WHERE product_id = ? AND sale_id = ?",
            (self.product_id, sale_id),
        )
        self.assertEqual([row["change_qty"] for row in moves], [-2])

    def test_goods_sold_in_the_meantime_stop_the_collection(self):
        # Walk-ins bought four of the five while two sat in the back room;
        # only one is left to give anybody.
        products_service.adjust_stock(self.product_id, -4, reason="Sale")
        with self.assertRaises(sales_service.SaleError):
            service.collect(self.layaway_id, self.admin.user_id, amount_paid="15.00")
        # And the layaway is still held, still owed.
        self.assertEqual(service.get_layaway(self.layaway_id)["status"], "Held")

    def test_a_collected_layaway_cannot_be_collected_again(self):
        service.collect(self.layaway_id, self.admin.user_id, amount_paid="15.00")
        with self.assertRaises(service.LayawayError):
            service.collect(self.layaway_id, self.admin.user_id, amount_paid="15.00")

    def test_the_frozen_price_is_not_recorded_as_an_override(self):
        service.collect(self.layaway_id, self.admin.user_id, amount_paid="15.00")
        overrides = db.query(
            "SELECT * FROM audit_log WHERE action = 'Price overridden'"
        )
        self.assertEqual(overrides, [])


class CancelTests(DatabaseTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.product_id = products_service.create_product(
            sku="S1", name="Widget", price_usd="10.00", stock_qty=5
        )
        cart = sales_service.Cart()
        cart.add_product(products_service.get_product(self.product_id), 2)
        self.layaway_id = service.hold(cart, self.admin.user_id, deposit="5.00")

    def test_cancelling_refunds_the_deposit_out_of_the_drawer(self):
        before = shifts_service.totals(self.shift_id)["expected_usd"]
        service.cancel(self.layaway_id, self.admin.user_id)
        after = shifts_service.totals(self.shift_id)["expected_usd"]
        self.assertEqual(after, before - 5.0)
        self.assertEqual(service.get_layaway(self.layaway_id)["status"], "Cancelled")

    def test_a_zero_deposit_cancels_quietly(self):
        cart = sales_service.Cart()
        cart.add_product(products_service.get_product(self.product_id), 1)
        layaway_id = service.hold(cart, self.admin.user_id)
        service.cancel(layaway_id, self.admin.user_id)
        self.assertEqual(service.get_layaway(layaway_id)["status"], "Cancelled")

    def test_cancelling_needs_a_drawer_to_refund_into(self):
        shifts_service.close_shift(self.shift_id, self.admin.user_id, counted_usd=100)
        with self.assertRaises(service.LayawayError):
            service.cancel(self.layaway_id, self.admin.user_id)
        # Refused before anything moved: still held, deposit intact.
        self.assertEqual(service.get_layaway(self.layaway_id)["status"], "Held")


class SummaryTests(DatabaseTestCase):
    def test_the_summary_counts_what_is_still_owed(self):
        product_id = products_service.create_product(
            sku="S1", name="Widget", price_usd="10.00", stock_qty=5
        )
        cart = sales_service.Cart()
        cart.add_product(products_service.get_product(product_id), 1)
        service.hold(cart, self.admin.user_id, deposit="4.00")
        totals = service.summary()
        self.assertEqual(totals["held"], 1)
        self.assertEqual(totals["value_usd"], 6.0)


if __name__ == "__main__":
    unittest.main()
