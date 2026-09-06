"""The sale pipeline: cart, checkout, invoice numbering, refunds."""

from __future__ import annotations

import unittest
from decimal import Decimal

from app import config, db
from app.services import customers as customers_service
from app.services import products as products_service
from app.services import sales as service
from app.services import settings as settings_service
from tests.support import DatabaseTestCase


class CartTests(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        self.product_id = products_service.create_product(
            sku="S1", name="Widget", price_usd="4.50", cost_usd="2.00", stock_qty=5
        )
        self.product = products_service.get_product(self.product_id)

    def test_adding_the_same_product_twice_merges_the_line(self):
        cart = service.Cart()
        cart.add_product(self.product, 2)
        cart.add_product(self.product, 1)
        self.assertEqual(len(cart.lines), 1)
        self.assertEqual(cart.item_count, 3)

    def test_cannot_add_more_than_stock(self):
        cart = service.Cart()
        with self.assertRaises(service.SaleError):
            cart.add_product(self.product, 6)

    def test_cannot_exceed_stock_across_two_adds(self):
        cart = service.Cart()
        cart.add_product(self.product, 4)
        with self.assertRaises(service.SaleError):
            cart.add_product(self.product, 2)

    def test_setting_quantity_to_zero_removes_the_line(self):
        cart = service.Cart()
        cart.add_product(self.product, 2)
        cart.set_qty(self.product_id, 0)
        self.assertTrue(cart.is_empty)

    def test_line_total_uses_the_captured_price(self):
        cart = service.Cart()
        line = cart.add_product(self.product, 3)
        self.assertEqual(line.line_total, Decimal("13.50"))

    def test_a_category_rate_travels_with_the_line(self):
        category_id = products_service.create_category("Essentials", tax_rate="5")
        product_id = products_service.create_product(
            sku="S2", name="Bread", price_usd="1.00", stock_qty=5,
            category_id=category_id,
        )
        cart = service.Cart()
        cart.add_product(products_service.get_product(product_id), 2)
        cart.add_product(self.product, 1)  # uncategorised: store-wide rate
        settings_service.set_value("tax_rate", "10")

        subtotal, _discount, tax, total = cart.totals()
        self.assertEqual(subtotal, Decimal("6.50"))
        # 5% on the bread, the store's 10% on the widget.
        self.assertEqual(tax, Decimal("0.55"))
        self.assertEqual(total, Decimal("7.05"))

    def test_a_sale_stores_the_mixed_tax(self):
        category_id = products_service.create_category("Essentials", tax_rate="5")
        product_id = products_service.create_product(
            sku="S2", name="Bread", price_usd="1.00", stock_qty=5,
            category_id=category_id,
        )
        settings_service.set_value("tax_rate", "10")
        cart = service.Cart()
        cart.add_product(products_service.get_product(product_id), 2)
        cart.add_product(self.product, 1)
        sale_id = service.create_sale(
            cart=cart, user_id=self.admin.user_id,
            payment_method="Cash", amount_paid="20",
        )
        sale = service.get_sale(sale_id)
        self.assertEqual(sale["tax_usd"], 0.55)
        self.assertEqual(sale["total_usd"], 7.05)


class CheckoutTests(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        self.product_id = products_service.create_product(
            sku="S1", name="Widget", price_usd="4.50", cost_usd="2.00", stock_qty=10
        )
        self.product = products_service.get_product(self.product_id)

    def sell(self, qty=2, **kwargs):
        cart = service.Cart()
        cart.add_product(self.product, qty)
        cart.discount = kwargs.pop("discount", 0)
        kwargs.setdefault("amount_paid", 100)
        return service.create_sale(user_id=self.admin.user_id, cart=cart, **kwargs)

    def test_sale_is_written_with_totals(self):
        sale = service.get_sale(self.sell(qty=2))
        self.assertEqual(sale["subtotal_usd"], 9.00)
        self.assertEqual(sale["total_usd"], 9.00)
        self.assertEqual(sale["change_usd"], 91.00)
        self.assertEqual(sale["status"], config.SALE_COMPLETED)

    def test_stock_is_decremented(self):
        self.sell(qty=3)
        self.assertEqual(products_service.get_product(self.product_id)["stock_qty"], 7)

    def test_sale_item_captures_name_sku_and_cost(self):
        items = service.get_sale_items(self.sell(qty=1))
        self.assertEqual(items[0]["name_at_sale"], "Widget")
        self.assertEqual(items[0]["sku_at_sale"], "S1")
        self.assertEqual(items[0]["cost_usd"], 2.00)

    def test_empty_cart_is_refused(self):
        with self.assertRaises(service.SaleError):
            service.create_sale(user_id=self.admin.user_id, cart=service.Cart())

    def test_insufficient_cash_is_refused(self):
        with self.assertRaises(service.SaleError):
            self.sell(qty=2, amount_paid=1)

    def test_card_payment_does_not_require_cash_tendered(self):
        sale = service.get_sale(self.sell(qty=2, payment_method="Card", amount_paid=0))
        self.assertEqual(sale["payment_method"], "Card")
        self.assertEqual(sale["change_usd"], 0.0)

    def test_unknown_payment_method_is_refused(self):
        with self.assertRaises(service.SaleError):
            self.sell(payment_method="Bitcoin")

    def test_lbp_payment_converts_to_usd_change(self):
        settings_service.set_value("exchange_rate", "90000")
        sale_id = self.sell(qty=2, paid_currency="LBP", amount_paid=900000)
        sale = service.get_sale(sale_id)
        self.assertEqual(sale["paid_currency"], "LBP")
        self.assertEqual(sale["change_usd"], 1.00)  # $10 tendered against $9

    def test_exchange_rate_is_stored_on_the_sale(self):
        settings_service.set_value("exchange_rate", "90000")
        sale = service.get_sale(self.sell())
        self.assertEqual(sale["exchange_rate"], 90000)

    def test_zero_exchange_rate_is_refused(self):
        with self.assertRaises(service.SaleError):
            self.sell(exchange_rate=0)

    def test_discount_and_tax_are_recorded(self):
        sale = service.get_sale(self.sell(qty=2, discount="1.00", tax_rate="10"))
        self.assertEqual(sale["discount_usd"], 1.00)
        self.assertEqual(sale["tax_usd"], 0.80)
        self.assertEqual(sale["total_usd"], 8.80)

    def test_sale_is_linked_to_a_customer(self):
        customer_id = customers_service.create_customer(name="Rami")
        sale = service.get_sale(self.sell(customer_id=customer_id))
        self.assertEqual(sale["customer_name"], "Rami")

    def test_stock_movement_is_linked_to_the_sale(self):
        sale_id = self.sell(qty=2)
        log = db.query_one(
            "SELECT * FROM inventory_log WHERE reason = 'Sale' ORDER BY log_id DESC"
        )
        self.assertEqual(log["sale_id"], sale_id)
        self.assertEqual(log["change_qty"], -2)

    def test_selling_more_than_stock_rolls_everything_back(self):
        cart = service.Cart()
        cart.add_product(self.product, 4)
        # Someone else empties the shelf between adding to the cart and paying.
        products_service.adjust_stock(self.product_id, -8, "Adjustment")
        with self.assertRaises(service.SaleError):
            service.create_sale(user_id=self.admin.user_id, cart=cart, amount_paid=100)
        self.assertEqual(products_service.get_product(self.product_id)["stock_qty"], 2)
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM sales", default=0), 0)


class InvoiceNumberTests(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        self.product = products_service.get_product(
            products_service.create_product(
                sku="S1", name="Widget", price_usd="1.00", stock_qty=100
            )
        )

    def sell(self):
        cart = service.Cart()
        cart.add_product(self.product, 1)
        return service.create_sale(
            user_id=self.admin.user_id, cart=cart, amount_paid=10
        )

    def test_numbers_increment_within_the_day(self):
        numbers = [service.get_sale(self.sell())["invoice_no"] for _ in range(3)]
        self.assertTrue(all(n.startswith("INV-") for n in numbers))
        self.assertEqual([n[-4:] for n in numbers], ["0001", "0002", "0003"])

    def test_numbers_are_unique_after_a_refund(self):
        first = self.sell()
        service.refund_sale(first, self.admin.user_id)
        second = service.get_sale(self.sell())["invoice_no"]
        self.assertEqual(second[-4:], "0002")


class RefundTests(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        self.product_id = products_service.create_product(
            sku="S1", name="Widget", price_usd="4.50", cost_usd="2.00", stock_qty=10
        )
        cart = service.Cart()
        cart.add_product(products_service.get_product(self.product_id), 3)
        self.sale_id = service.create_sale(
            user_id=self.admin.user_id, cart=cart, amount_paid=100
        )

    def test_refund_restores_stock(self):
        service.refund_sale(self.sale_id, self.admin.user_id)
        self.assertEqual(products_service.get_product(self.product_id)["stock_qty"], 10)

    def test_refund_marks_the_status(self):
        service.refund_sale(self.sale_id, self.admin.user_id, "damaged")
        self.assertEqual(service.get_sale(self.sale_id)["status"], config.SALE_REFUNDED)

    def test_refunding_twice_is_refused(self):
        service.refund_sale(self.sale_id, self.admin.user_id)
        with self.assertRaises(service.SaleError):
            service.refund_sale(self.sale_id, self.admin.user_id)

    def test_unknown_sale_is_refused(self):
        with self.assertRaises(service.SaleError):
            service.refund_sale(9999, self.admin.user_id)


class ListingTests(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        product = products_service.get_product(
            products_service.create_product(
                sku="S1", name="Widget", price_usd="1.00", stock_qty=100
            )
        )
        self.customer_id = customers_service.create_customer(name="Rami Saad")
        for _ in range(3):
            cart = service.Cart()
            cart.add_product(product, 1)
            service.create_sale(
                user_id=self.admin.user_id, cart=cart,
                customer_id=self.customer_id, amount_paid=10,
            )

    def test_lists_newest_first(self):
        rows = service.list_sales()
        self.assertEqual(len(rows), 3)
        self.assertGreater(rows[0]["sale_id"], rows[-1]["sale_id"])

    def test_search_by_customer(self):
        self.assertEqual(len(service.list_sales(search="Rami")), 3)
        self.assertEqual(len(service.list_sales(search="Nobody")), 0)

    def test_filter_by_status(self):
        service.refund_sale(service.list_sales()[0]["sale_id"], self.admin.user_id)
        self.assertEqual(len(service.list_sales(status=config.SALE_REFUNDED)), 1)
        self.assertEqual(len(service.list_sales(status=config.SALE_COMPLETED)), 2)

    def test_date_range_excludes_other_days(self):
        self.assertEqual(len(service.list_sales(date_from="2000-01-01", date_to="2000-01-02")), 0)


if __name__ == "__main__":
    unittest.main()
