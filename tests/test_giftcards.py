"""Gift cards: sold at the till, redeemed at the till, explained line by line."""

from __future__ import annotations

import sqlite3
import unittest
from decimal import Decimal

from app import db
from app.services import giftcards as service
from app.services import products as products_service
from app.services import sales as sales_service
from tests.support import DatabaseTestCase


class CodeTests(unittest.TestCase):
    def test_codes_have_the_shop_proof_shape(self):
        code = service.generate_code()
        self.assertRegex(code, r"^GC-[2-9A-HJ-KM-NP-Z]{4}-[2-9A-HJ-KM-NP-Z]{4}$")

    def test_codes_are_not_reused(self):
        codes = {service.generate_code() for _ in range(50)}
        self.assertEqual(len(codes), 50)

    def test_lookup_ignores_case_and_whitespace(self):
        code = service.generate_code()
        self.assertEqual(service.normalise(f"  {code.lower()} "), code)


class IssueTests(DatabaseTestCase):
    opens_shift = False

    def test_a_issued_card_holds_what_it_was_given(self):
        card = service.issue("25.00", user_id=self.admin.user_id)
        self.assertEqual(Decimal(str(card["balance_usd"])), Decimal("25.00"))
        self.assertEqual(service.balance(card["code"])["balance_usd"], 25.0)

    def test_nothing_is_issued_for_nothing(self):
        with self.assertRaises(service.GiftCardError):
            service.issue("0")

    def test_the_history_shows_the_issue(self):
        card = service.issue("10.00", user_id=self.admin.user_id, note="Rami")
        events = service.history(card["card_id"])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["kind"], "Issue")
        self.assertEqual(events[0]["note"], "Rami")


class RedeemTests(DatabaseTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.card = service.issue("20.00")

    def test_a_sale_can_be_paid_from_a_card(self):
        product_id = products_service.create_product(
            sku="S1", name="Widget", price_usd="12.00", stock_qty=5
        )
        cart = sales_service.Cart()
        cart.add_product(products_service.get_product(product_id), 1)
        sale_id = sales_service.create_sale(
            cart=cart, user_id=self.admin.user_id,
            payment_method="Cash", amount_paid="0",
            gift_card_code=self.card["code"],
        )
        sale = sales_service.get_sale(sale_id)
        self.assertEqual(sale["total_usd"], 12.0)
        # The card paid the lot; the till is owed nothing and holds nothing.
        self.assertEqual(service.balance(self.card["code"])["balance_usd"], 8.0)

    def test_a_partial_redemption_leaves_the_rest_for_cash(self):
        product_id = products_service.create_product(
            sku="S1", name="Widget", price_usd="30.00", stock_qty=5
        )
        cart = sales_service.Cart()
        cart.add_product(products_service.get_product(product_id), 1)
        sale_id = sales_service.create_sale(
            cart=cart, user_id=self.admin.user_id,
            payment_method="Cash", amount_paid="15.00",
            gift_card_code=self.card["code"],
        )
        self.assertEqual(sales_service.get_sale(sale_id)["change_usd"], 5.0)
        self.assertEqual(service.balance(self.card["code"])["balance_usd"], 0.0)
        self.assertEqual(service.get_by_id(self.card["card_id"])["status"], "Empty")

    def test_a_card_cannot_spend_more_than_it_holds(self):
        product_id = products_service.create_product(
            sku="S1", name="Widget", price_usd="30.00", stock_qty=5
        )
        cart = sales_service.Cart()
        cart.add_product(products_service.get_product(product_id), 1)
        with self.assertRaises(sales_service.SaleError):
            sales_service.create_sale(
                cart=cart, user_id=self.admin.user_id,
                payment_method="Cash", amount_paid="0",
                gift_card_code=self.card["code"],
            )
        # Nothing moved: not the stock, not the balance, not the invoices.
        self.assertEqual(service.balance(self.card["code"])["balance_usd"], 20.0)
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM sales", default=0), 0)

    def test_an_empty_card_is_refused(self):
        product_id = products_service.create_product(
            sku="S1", name="Widget", price_usd="5.00", stock_qty=5
        )
        service.set_status(self.card["card_id"], service.EMPTY)
        cart = sales_service.Cart()
        cart.add_product(products_service.get_product(product_id), 1)
        with self.assertRaises(sales_service.SaleError):
            sales_service.create_sale(
                cart=cart, user_id=self.admin.user_id,
                payment_method="Cash", amount_paid="0",
                gift_card_code=self.card["code"],
            )

    def test_a_disabled_card_is_refused(self):
        product_id = products_service.create_product(
            sku="S1", name="Widget", price_usd="5.00", stock_qty=5
        )
        service.set_status(self.card["card_id"], service.DISABLED)
        cart = sales_service.Cart()
        cart.add_product(products_service.get_product(product_id), 1)
        with self.assertRaises(sales_service.SaleError):
            sales_service.create_sale(
                cart=cart, user_id=self.admin.user_id,
                payment_method="Cash", amount_paid="0",
                gift_card_code=self.card["code"],
            )

    def test_a_card_cannot_pay_for_a_card(self):
        cart = sales_service.Cart()
        cart.add_gift_card(service.generate_code(), "20.00")
        with self.assertRaises(sales_service.SaleError):
            sales_service.create_sale(
                cart=cart, user_id=self.admin.user_id,
                payment_method="Cash", amount_paid="0",
                gift_card_code=self.card["code"],
            )

    def test_a_sold_card_comes_alive_with_the_sale(self):
        cart = sales_service.Cart()
        code = service.generate_code()
        cart.add_gift_card(code, "50.00")
        sale_id = sales_service.create_sale(
            cart=cart, user_id=self.admin.user_id,
            payment_method="Cash", amount_paid="50.00",
        )
        card = service.balance(code)
        self.assertEqual(card["balance_usd"], 50.0)
        self.assertEqual(card["sold_sale_id"], sale_id)
        events = service.history(card["card_id"])
        self.assertEqual([event["kind"] for event in events], ["Sale"])

    def test_a_rolled_back_sale_never_leaves_a_card_behind(self):
        # Two cards cannot share a code: hand the cart a code that is already
        # live and the sale's commit must fail with nothing left behind — not
        # the sale, not the stock, and not a half-born card.
        existing = service.list_cards()[0]["code"]
        code = service.generate_code()
        cart = sales_service.Cart()
        cart.add_gift_card(code, "50.00")
        cart.lines[0].gift_code = existing
        product_id = products_service.create_product(
            sku="S1", name="Widget", price_usd="5.00", stock_qty=5
        )
        cart.add_product(products_service.get_product(product_id), 1)
        with self.assertRaises(sqlite3.IntegrityError):
            sales_service.create_sale(
                cart=cart, user_id=self.admin.user_id,
                payment_method="Cash", amount_paid="55.00",
                gift_card_code="",
            )
        self.assertIsNone(service.get_by_code(code))
        self.assertEqual(products_service.get_product(product_id)["stock_qty"], 5)
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM sales", default=0), 0)

    def test_the_liability_is_what_is_still_owed(self):
        self.assertEqual(service.outstanding_liability(), 20.0)
        cart = sales_service.Cart()
        product_id = products_service.create_product(
            sku="S1", name="Widget", price_usd="20.00", stock_qty=5
        )
        cart.add_product(products_service.get_product(product_id), 1)
        sales_service.create_sale(
            cart=cart, user_id=self.admin.user_id,
            payment_method="Cash", amount_paid="0",
            gift_card_code=self.card["code"],
        )
        self.assertEqual(service.outstanding_liability(), 0.0)
