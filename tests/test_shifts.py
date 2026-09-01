"""Till shifts, cash movements and end-of-day reconciliation."""

from __future__ import annotations

import unittest

from app import config
from app.services import products as products_service
from app.services import sales as sales_service
from app.services import settings as settings_service
from app.services import shifts as service
from tests.support import DatabaseTestCase


class OpeningTests(DatabaseTestCase):
    opens_shift = False

    def test_opening_records_the_float(self):
        shift_id = service.open_shift(self.admin.user_id, opening_float="50.00")
        shift = service.get_shift(shift_id)
        self.assertEqual(shift["opening_float_usd"], 50.0)
        self.assertEqual(shift["status"], config.SHIFT_OPEN)

    def test_only_one_shift_may_be_open(self):
        service.open_shift(self.admin.user_id, opening_float=10)
        with self.assertRaises(service.ShiftError):
            service.open_shift(self.admin.user_id, opening_float=10)

    def test_negative_float_is_refused(self):
        with self.assertRaises(service.ShiftError):
            service.open_shift(self.admin.user_id, opening_float="-1")

    def test_selling_without_a_shift_is_refused(self):
        product_id = products_service.create_product(
            sku="S1", name="Widget", price_usd="5.00", stock_qty=10
        )
        cart = sales_service.Cart()
        cart.add_product(products_service.get_product(product_id), 1)
        with self.assertRaises(service.ShiftError):
            sales_service.create_sale(
                user_id=self.admin.user_id, cart=cart, amount_paid=10
            )

    def test_the_requirement_can_be_switched_off(self):
        settings_service.set_value("require_shift", "0")
        product_id = products_service.create_product(
            sku="S1", name="Widget", price_usd="5.00", stock_qty=10
        )
        cart = sales_service.Cart()
        cart.add_product(products_service.get_product(product_id), 1)
        sale_id = sales_service.create_sale(
            user_id=self.admin.user_id, cart=cart, amount_paid=10
        )
        self.assertIsNone(sales_service.get_sale(sale_id)["shift_id"])


class DrawerTests(DatabaseTestCase):
    """The harness opens a shift with a $100 float."""

    def setUp(self):
        super().setUp()
        self.product = products_service.get_product(
            products_service.create_product(
                sku="S1", name="Widget", price_usd="10.00", cost_usd="4.00", stock_qty=50
            )
        )

    def sell(self, qty=1, payment_method="Cash"):
        cart = sales_service.Cart()
        cart.add_product(self.product, qty)
        return sales_service.create_sale(
            user_id=self.admin.user_id, cart=cart,
            payment_method=payment_method, amount_paid=1000,
        )

    def test_sales_are_linked_to_the_open_shift(self):
        sale_id = self.sell()
        self.assertEqual(sales_service.get_sale(sale_id)["shift_id"], self.shift_id)

    def test_only_cash_sales_reach_the_drawer(self):
        self.sell(2)                      # 20.00 cash
        self.sell(3, payment_method="Card")  # 30.00 card
        totals = service.totals(self.shift_id)
        self.assertEqual(totals["cash_sales"], 20.0)
        self.assertEqual(totals["non_cash_sales"], 30.0)
        self.assertEqual(totals["expected_usd"], 120.0)

    def test_cash_in_and_out_move_the_expected_total(self):
        service.add_cash_movement(
            self.shift_id, self.admin.user_id, config.CASH_IN, "25.00", "Owner deposit"
        )
        service.add_cash_movement(
            self.shift_id, self.admin.user_id, config.CASH_OUT, "40.00", "Bank drop"
        )
        self.assertEqual(service.totals(self.shift_id)["expected_usd"], 85.0)

    def test_taking_out_more_than_the_drawer_holds_is_refused(self):
        with self.assertRaises(service.ShiftError):
            service.add_cash_movement(
                self.shift_id, self.admin.user_id, config.CASH_OUT, "500", "Bank drop"
            )

    def test_zero_movements_are_refused(self):
        with self.assertRaises(service.ShiftError):
            service.add_cash_movement(
                self.shift_id, self.admin.user_id, config.CASH_IN, "0", "Nothing"
            )

    def test_a_cash_refund_comes_back_out_of_the_drawer(self):
        sale_id = self.sell(2)  # +20.00
        sales_service.refund_sale(sale_id, self.admin.user_id)
        self.assertEqual(service.totals(self.shift_id)["expected_usd"], 100.0)


class ClosingTests(DatabaseTestCase):
    def test_an_exact_count_closes_with_no_variance(self):
        summary = service.close_shift(
            self.shift_id, self.admin.user_id, counted_usd="100.00"
        )
        self.assertEqual(summary["variance_usd"], 0.0)
        self.assertEqual(
            service.get_shift(self.shift_id)["status"], config.SHIFT_CLOSED
        )

    def test_a_short_drawer_records_a_negative_variance(self):
        summary = service.close_shift(
            self.shift_id, self.admin.user_id, counted_usd="90.00"
        )
        self.assertEqual(summary["variance_usd"], -10.0)

    def test_counted_lbp_is_converted_at_the_shifts_own_rate(self):
        rate = service.get_shift(self.shift_id)["exchange_rate"]
        summary = service.close_shift(
            self.shift_id, self.admin.user_id,
            counted_usd="50.00", counted_lbp=rate * 50,  # another $50 in lira
        )
        self.assertEqual(summary["variance_usd"], 0.0)

    def test_closing_twice_is_refused(self):
        service.close_shift(self.shift_id, self.admin.user_id, counted_usd="100")
        with self.assertRaises(service.ShiftError):
            service.close_shift(self.shift_id, self.admin.user_id, counted_usd="100")

    def test_a_closed_shift_frees_the_till(self):
        service.close_shift(self.shift_id, self.admin.user_id, counted_usd="100")
        self.assertIsNone(service.current_shift())
        service.open_shift(self.admin.user_id, opening_float=10)


if __name__ == "__main__":
    unittest.main()
