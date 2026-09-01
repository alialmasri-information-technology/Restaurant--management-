"""Partial and full returns, and how they are priced.

The interesting case throughout is the invoice-level discount: a customer who
got $10 off a $40 basket and brings one of four units back is owed the
discounted price of that unit, not the shelf price.
"""

from __future__ import annotations

import unittest

from app import config
from app.money import D
from app.services import products as products_service
from app.services import returns as service
from app.services import sales as sales_service
from tests.support import DatabaseTestCase


class ReturnTestCase(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        self.product = products_service.get_product(
            products_service.create_product(
                sku="S1", name="Widget", price_usd="10.00", cost_usd="4.00", stock_qty=50
            )
        )

    def sell(self, qty=4, discount="0", tax_rate="0"):
        cart = sales_service.Cart()
        cart.add_product(self.product, qty)
        cart.discount = D(discount)
        return sales_service.create_sale(
            user_id=self.admin.user_id, cart=cart,
            amount_paid=1000, tax_rate=tax_rate,
        )

    def line_ids(self, sale_id):
        return [row["sale_item_id"] for row in service.returnable_lines(sale_id)]


class QuoteTests(ReturnTestCase):
    def test_a_plain_return_refunds_the_unit_price(self):
        sale_id = self.sell(qty=3)
        line = self.line_ids(sale_id)[0]
        quoted = service.quote(sale_id, {line: 1})
        self.assertEqual(quoted["total_usd"], 10.0)

    def test_an_invoice_discount_is_prorated_across_the_returned_units(self):
        # 4 x 10.00 = 40.00, less 10.00 off, plus 10% tax on 30.00 = 33.00.
        sale_id = self.sell(qty=4, discount="10.00", tax_rate="10")
        line = self.line_ids(sale_id)[0]
        quoted = service.quote(sale_id, {line: 1})
        self.assertEqual(quoted["line_value_usd"], 10.0)
        self.assertEqual(quoted["discount_share_usd"], 2.50)
        self.assertEqual(quoted["tax_share_usd"], 0.75)
        self.assertEqual(quoted["total_usd"], 8.25)

    def test_returning_everything_refunds_exactly_what_was_paid(self):
        sale_id = self.sell(qty=4, discount="10.00", tax_rate="10")
        total_paid = sales_service.get_sale(sale_id)["total_usd"]
        quoted = service.quote(sale_id, {self.line_ids(sale_id)[0]: 4})
        self.assertEqual(quoted["total_usd"], total_paid)

    def test_more_than_was_bought_is_refused(self):
        sale_id = self.sell(qty=2)
        with self.assertRaises(service.ReturnError):
            service.quote(sale_id, {self.line_ids(sale_id)[0]: 3})

    def test_an_empty_return_is_refused(self):
        sale_id = self.sell(qty=2)
        with self.assertRaises(service.ReturnError):
            service.quote(sale_id, {self.line_ids(sale_id)[0]: 0})

    def test_a_line_from_another_invoice_is_refused(self):
        first = self.sell(qty=1)
        second = self.sell(qty=1)
        with self.assertRaises(service.ReturnError):
            service.quote(second, {self.line_ids(first)[0]: 1})

    def test_quoting_writes_nothing(self):
        sale_id = self.sell(qty=3)
        service.quote(sale_id, {self.line_ids(sale_id)[0]: 2})
        self.assertEqual(service.returnable_lines(sale_id)[0]["remaining_qty"], 3)
        self.assertEqual(len(service.list_returns()), 0)


class CreateReturnTests(ReturnTestCase):
    def test_stock_comes_back(self):
        sale_id = self.sell(qty=4)
        self.assertEqual(products_service.get_product(self.product["product_id"])["stock_qty"], 46)
        service.create_return(sale_id, self.admin.user_id, {self.line_ids(sale_id)[0]: 3})
        self.assertEqual(products_service.get_product(self.product["product_id"])["stock_qty"], 49)

    def test_faulty_goods_can_be_refunded_without_restocking(self):
        sale_id = self.sell(qty=2)
        service.create_return(
            sale_id, self.admin.user_id, {self.line_ids(sale_id)[0]: 2},
            reason="Faulty", restock=False,
        )
        self.assertEqual(products_service.get_product(self.product["product_id"])["stock_qty"], 48)

    def test_a_partial_return_leaves_the_invoice_completed(self):
        sale_id = self.sell(qty=4)
        service.create_return(sale_id, self.admin.user_id, {self.line_ids(sale_id)[0]: 1})
        sale = sales_service.get_sale(sale_id)
        self.assertEqual(sale["status"], config.SALE_COMPLETED)
        self.assertEqual(sales_service.display_status(sale), "Part returned")

    def test_returning_the_last_unit_refunds_the_invoice(self):
        sale_id = self.sell(qty=2)
        line = self.line_ids(sale_id)[0]
        service.create_return(sale_id, self.admin.user_id, {line: 1})
        service.create_return(sale_id, self.admin.user_id, {line: 1})
        self.assertEqual(sales_service.get_sale(sale_id)["status"], config.SALE_REFUNDED)

    def test_returns_accumulate_against_the_line(self):
        sale_id = self.sell(qty=5)
        line = self.line_ids(sale_id)[0]
        service.create_return(sale_id, self.admin.user_id, {line: 2})
        self.assertEqual(service.returnable_lines(sale_id)[0]["remaining_qty"], 3)
        service.create_return(sale_id, self.admin.user_id, {line: 3})
        self.assertEqual(service.returnable_lines(sale_id)[0]["remaining_qty"], 0)
        with self.assertRaises(service.ReturnError):
            service.create_return(sale_id, self.admin.user_id, {line: 1})

    def test_return_numbers_increment(self):
        first = self.sell(qty=1)
        second = self.sell(qty=1)
        one = service.get_return(
            service.create_return(first, self.admin.user_id, {self.line_ids(first)[0]: 1})
        )
        two = service.get_return(
            service.create_return(second, self.admin.user_id, {self.line_ids(second)[0]: 1})
        )
        self.assertNotEqual(one["return_no"], two["return_no"])
        self.assertLess(one["return_no"], two["return_no"])

    def test_an_unknown_refund_method_is_refused(self):
        sale_id = self.sell(qty=1)
        with self.assertRaises(service.ReturnError):
            service.create_return(
                sale_id, self.admin.user_id, {self.line_ids(sale_id)[0]: 1},
                refund_method="Cheque",
            )

    def test_the_return_is_linked_to_the_open_shift(self):
        sale_id = self.sell(qty=1)
        return_id = service.create_return(
            sale_id, self.admin.user_id, {self.line_ids(sale_id)[0]: 1}
        )
        self.assertEqual(service.get_return(return_id)["shift_id"], self.shift_id)

    def test_items_are_recorded_against_the_return(self):
        sale_id = self.sell(qty=3)
        return_id = service.create_return(
            sale_id, self.admin.user_id, {self.line_ids(sale_id)[0]: 2}
        )
        items = service.get_return_items(return_id)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["qty"], 2)
        self.assertEqual(items[0]["name_at_sale"], "Widget")


if __name__ == "__main__":
    unittest.main()
