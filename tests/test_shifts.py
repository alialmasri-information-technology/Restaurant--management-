"""Till shifts, cash movements and end-of-day reconciliation."""

from __future__ import annotations

import unittest

from app import config
from app.services import giftcards as giftcards_service
from app.services import layaways as layaways_service
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


class MoneyPaidBeforeTheDrawerTests(DatabaseTestCase):
    """What the drawer expects must be what the cashier can count.

    The expected figure took the full value of every invoice marked Cash. Two
    kinds of money are paid before the cashier ever opens the drawer, and both
    were counted again:

      * a gift card, which pays part of the total off the card's own balance;
      * a layaway deposit, banked when the goods were set aside and already a
        cash movement in that shift.

    Either one leaves the till short by that amount at close -- and the
    shortfall is written into the shift as the variance of whoever was on it,
    which is a bad thing to be wrong about.

    The harness opens a shift with a $100 float.
    """

    def setUp(self):
        super().setUp()
        self.product = products_service.get_product(
            products_service.create_product(
                sku="S1", name="Widget", price_usd="50.00", cost_usd="10.00",
                stock_qty=50,
            )
        )

    def cart(self):
        cart = sales_service.Cart()
        cart.add_product(self.product, 1)
        return cart

    def test_a_gift_card_does_not_put_money_in_the_drawer(self):
        card = giftcards_service.issue(30, user_id=self.admin.user_id)
        sales_service.create_sale(
            user_id=self.admin.user_id, cart=self.cart(), payment_method="Cash",
            gift_card_code=card["code"], gift_card_amount=30, amount_paid=20,
        )
        totals = service.totals(self.shift_id)

        self.assertEqual(totals["cash_sales"], 20.0, "the card's 30.00 was counted as cash")
        self.assertEqual(totals["expected_usd"], 120.0)

    def test_a_card_that_pays_the_whole_sale_leaves_the_drawer_alone(self):
        card = giftcards_service.issue(50, user_id=self.admin.user_id)
        sales_service.create_sale(
            user_id=self.admin.user_id, cart=self.cart(), payment_method="Cash",
            gift_card_code=card["code"], gift_card_amount=50, amount_paid=0,
        )
        self.assertEqual(service.totals(self.shift_id)["expected_usd"], 100.0)

    def test_a_layaway_deposit_is_not_taken_twice(self):
        """It was banked as a cash movement when the goods were set aside."""
        layaway_id = layaways_service.hold(
            cart=self.cart(), customer_id=None, deposit=20,
            user_id=self.admin.user_id,
        )
        self.assertEqual(service.totals(self.shift_id)["cash_in"], 20.0)

        layaways_service.collect(
            layaway_id, self.admin.user_id, payment_method="Cash", amount_paid=30,
        )
        totals = service.totals(self.shift_id)

        self.assertEqual(totals["cash_sales"], 30.0, "the deposit was counted again")
        self.assertEqual(totals["expected_usd"], 150.0, "100 float + 20 deposit + 30")

    def test_a_deposit_taken_on_the_card_machine_is_still_not_cash(self):
        layaway_id = layaways_service.hold(
            cart=self.cart(), customer_id=None, deposit=20, deposit_method="Card",
            user_id=self.admin.user_id,
        )
        layaways_service.collect(
            layaway_id, self.admin.user_id, payment_method="Cash", amount_paid=30,
        )
        totals = service.totals(self.shift_id)

        self.assertEqual(totals["cash_in"], 0.0, "a card deposit is not a cash movement")
        self.assertEqual(totals["cash_sales"], 30.0)
        self.assertEqual(totals["expected_usd"], 130.0, "100 float + 30 balance")

    def test_change_given_back_is_not_expected_in_the_drawer(self):
        """The ordinary case, kept here so the fix cannot break it."""
        sales_service.create_sale(
            user_id=self.admin.user_id, cart=self.cart(),
            payment_method="Cash", amount_paid=60,
        )
        self.assertEqual(service.totals(self.shift_id)["expected_usd"], 150.0)

    def test_a_shift_with_all_of_them_at_once_still_balances(self):
        card = giftcards_service.issue(30, user_id=self.admin.user_id)
        sales_service.create_sale(
            user_id=self.admin.user_id, cart=self.cart(), payment_method="Cash",
            gift_card_code=card["code"], gift_card_amount=30, amount_paid=20,
        )
        sales_service.create_sale(
            user_id=self.admin.user_id, cart=self.cart(),
            payment_method="Cash", amount_paid=60,
        )
        sales_service.create_sale(
            user_id=self.admin.user_id, cart=self.cart(),
            payment_method="Card", amount_paid=50,
        )
        layaway_id = layaways_service.hold(
            cart=self.cart(), customer_id=None, deposit=20,
            user_id=self.admin.user_id,
        )
        layaways_service.collect(
            layaway_id, self.admin.user_id, payment_method="Cash", amount_paid=30,
        )

        counted = 100 + 20 + 50 + 20 + 30  # float, then every note that changed hands
        summary = service.close_shift(
            self.shift_id, self.admin.user_id, counted_usd=counted, counted_lbp=0,
        )
        self.assertEqual(summary["variance_usd"], 0.0)


if __name__ == "__main__":
    unittest.main()
