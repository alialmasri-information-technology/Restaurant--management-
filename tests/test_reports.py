"""Reporting aggregates and PDF receipt generation."""

from __future__ import annotations

import unittest

from app import receipts
from app.services import customers as customers_service
from app.services import products as products_service
from app.services import reports as service
from app.services import returns as returns_service
from app.services import sales as sales_service
from app.services import settings as settings_service
from tests.support import DatabaseTestCase


class ReportTestCase(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        self.widget = products_service.get_product(
            products_service.create_product(
                sku="S1", name="Widget", price_usd="10.00", cost_usd="4.00", stock_qty=50
            )
        )
        self.gadget = products_service.get_product(
            products_service.create_product(
                sku="S2", name="Gadget", price_usd="2.00", cost_usd="1.00", stock_qty=50
            )
        )
        self.customer_id = customers_service.create_customer(name="Rami Saad")

    def sell(self, product, qty, **kwargs):
        cart = sales_service.Cart()
        cart.add_product(product, qty)
        cart.discount = kwargs.pop("discount", 0)
        kwargs.setdefault("amount_paid", 500)
        kwargs.setdefault("customer_id", self.customer_id)
        return sales_service.create_sale(user_id=self.admin.user_id, cart=cart, **kwargs)


class SummaryTests(ReportTestCase):
    def test_revenue_units_and_profit(self):
        self.sell(self.widget, 2)   # $20 revenue, $8 cost
        self.sell(self.gadget, 3)   # $6 revenue, $3 cost
        summary = service.summary()
        self.assertEqual(summary["sale_count"], 2)
        self.assertEqual(summary["revenue"], 26.0)
        self.assertEqual(summary["units"], 5)
        self.assertEqual(summary["gross_profit"], 15.0)
        self.assertEqual(summary["average_sale"], 13.0)

    def test_discount_reduces_profit(self):
        self.sell(self.widget, 2, discount="5")
        summary = service.summary()
        self.assertEqual(summary["revenue"], 15.0)
        self.assertEqual(summary["gross_profit"], 7.0)  # 20 - 8 cost - 5 discount

    def test_a_full_refund_is_netted_off_revenue(self):
        sale_id = self.sell(self.widget, 2)
        self.sell(self.gadget, 1)
        sales_service.refund_sale(sale_id, self.admin.user_id)
        summary = service.summary()
        self.assertEqual(summary["sale_count"], 1)
        self.assertEqual(summary["revenue"], 2.0)
        self.assertEqual(summary["refund_count"], 1)
        self.assertEqual(summary["refund_total"], 20.0)

    def test_a_partial_return_only_nets_off_the_units_that_came_back(self):
        sale_id = self.sell(self.widget, 3)   # $30 revenue, $12 cost
        line = returns_service.returnable_lines(sale_id)[0]["sale_item_id"]
        returns_service.create_return(sale_id, self.admin.user_id, {line: 1})

        summary = service.summary()
        # The invoice is still a sale; only the returned unit leaves the numbers.
        self.assertEqual(summary["sale_count"], 1)
        self.assertEqual(summary["gross_revenue"], 30.0)
        self.assertEqual(summary["revenue"], 20.0)
        self.assertEqual(summary["units"], 2)
        self.assertEqual(summary["gross_profit"], 12.0)
        self.assertEqual(summary["refund_total"], 10.0)

    def test_a_returned_unit_leaves_the_product_ranking(self):
        sale_id = self.sell(self.widget, 3)
        line = returns_service.returnable_lines(sale_id)[0]["sale_item_id"]
        returns_service.create_return(sale_id, self.admin.user_id, {line: 2})
        top = service.top_products()
        self.assertEqual(top[0]["units"], 1)
        self.assertEqual(top[0]["revenue"], 10.0)

    def test_returns_are_grouped_by_reason(self):
        sale_id = self.sell(self.widget, 2)
        line = returns_service.returnable_lines(sale_id)[0]["sale_item_id"]
        returns_service.create_return(
            sale_id, self.admin.user_id, {line: 1}, reason="Faulty"
        )
        breakdown = service.returns_breakdown()
        self.assertEqual(breakdown[0]["reason"], "Faulty")
        self.assertEqual(breakdown[0]["refunded"], 10.0)

    def test_empty_period_returns_zeros(self):
        summary = service.summary("2000-01-01", "2000-01-31")
        self.assertEqual(summary["sale_count"], 0)
        self.assertEqual(summary["revenue"], 0)
        self.assertEqual(summary["average_sale"], 0)

    def test_cost_is_frozen_at_the_time_of_sale(self):
        self.sell(self.widget, 1)
        products_service.update_product(
            self.widget["product_id"], sku="S1", name="Widget",
            price_usd="10.00", cost_usd="9.00",
        )
        self.assertEqual(service.summary()["gross_profit"], 6.0)


class BreakdownTests(ReportTestCase):
    def test_top_products_are_ranked_by_revenue(self):
        self.sell(self.gadget, 3)
        self.sell(self.widget, 2)
        top = service.top_products()
        self.assertEqual(top[0]["name"], "Widget")
        self.assertEqual(top[0]["units"], 2)

    def test_payment_breakdown(self):
        self.sell(self.widget, 1, payment_method="Cash")
        self.sell(self.gadget, 1, payment_method="Card", amount_paid=0)
        methods = {row["payment_method"]: row["sale_count"] for row in service.payment_breakdown()}
        self.assertEqual(methods, {"Cash": 1, "Card": 1})

    def test_top_customers_ignores_walk_ins(self):
        self.sell(self.widget, 1)
        self.sell(self.gadget, 1, customer_id=None)
        top = service.top_customers()
        self.assertEqual(len(top), 1)
        self.assertEqual(top[0]["name"], "Rami Saad")

    def test_daily_series_groups_by_day(self):
        self.sell(self.widget, 1)
        self.sell(self.gadget, 1)
        series = service.daily_series(service.today(), service.today())
        self.assertEqual(len(series), 1)
        self.assertEqual(series[0]["sale_count"], 2)

    def test_inventory_snapshot(self):
        snapshot = service.inventory_snapshot()
        self.assertEqual(snapshot["product_count"], 2)
        self.assertEqual(snapshot["units_in_stock"], 100)
        self.assertEqual(snapshot["stock_cost"], 250.0)  # 50*4 + 50*1


class ReceiptTests(ReportTestCase):
    def test_receipt_pdf_is_written(self):
        sale_id = self.sell(self.widget, 2)
        path = receipts.generate_receipt(sale_id)
        self.assertTrue(path.exists())
        self.assertGreater(path.stat().st_size, 500)
        self.assertEqual(path.read_bytes()[:4], b"%PDF")

    def test_receipt_grows_with_more_lines(self):
        small = receipts.generate_receipt(self.sell(self.widget, 1))
        small_size = small.stat().st_size
        cart = sales_service.Cart()
        cart.add_product(self.widget, 1)
        cart.add_product(self.gadget, 1)
        big_id = sales_service.create_sale(
            user_id=self.admin.user_id, cart=cart, amount_paid=500
        )
        self.assertGreater(receipts.generate_receipt(big_id).stat().st_size, small_size)

    def test_receipt_uses_the_rate_stored_on_the_sale(self):
        settings_service.set_value("exchange_rate", "90000")
        sale_id = self.sell(self.widget, 1)
        settings_service.set_value("exchange_rate", "1")
        path = receipts.generate_receipt(sale_id)
        self.assertTrue(path.exists())
        self.assertEqual(sales_service.get_sale(sale_id)["exchange_rate"], 90000)

    def test_unknown_sale_is_refused(self):
        with self.assertRaises(receipts.ReceiptError):
            receipts.generate_receipt(9999)

    def test_long_product_names_do_not_crash_the_layout(self):
        product = products_service.get_product(
            products_service.create_product(
                sku="LONG",
                name="Extra Large Deluxe Stainless Steel Vacuum Insulated Travel Mug",
                price_usd="12.00", stock_qty=5,
            )
        )
        path = receipts.generate_receipt(self.sell(product, 1))
        self.assertTrue(path.exists())


if __name__ == "__main__":
    unittest.main()
