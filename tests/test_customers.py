"""Customer records, and what deleting one is allowed to take with it.

This was the only service in the package with no test file of its own. It got
used constantly as a fixture — create_customer appears in seventeen other
tests — which is exactly why the gap was easy to miss: the module was
exercised all day and never actually examined.
"""

from __future__ import annotations

import unittest

from app import db
from app.services import accounts, customers, layaways
from app.services import products as products_service
from app.services import sales as sales_service
from tests.support import DatabaseTestCase


class ValidationTests(DatabaseTestCase):
    opens_shift = False

    def test_a_name_is_required(self):
        with self.assertRaises(customers.CustomerError):
            customers.create_customer(name="   ")

    def test_an_email_that_is_not_an_email_is_refused(self):
        for bad in ("nope", "a@b", "a b@c.com", "@example.com"):
            with self.subTest(email=bad), self.assertRaises(customers.CustomerError):
                customers.create_customer(name="Someone", email=bad)

    def test_no_email_at_all_is_fine(self):
        """Most walk-in customers never give one."""
        customer_id = customers.create_customer(name="Someone", email="")
        self.assertIsNotNone(customers.get_customer(customer_id))

    def test_the_same_rules_apply_when_editing(self):
        customer_id = customers.create_customer(name="Someone")
        with self.assertRaises(customers.CustomerError):
            customers.update_customer(customer_id, name="", email="")
        with self.assertRaises(customers.CustomerError):
            customers.update_customer(customer_id, name="Someone", email="nope")

    def test_editing_saves_and_trims(self):
        customer_id = customers.create_customer(name="Someone")
        customers.update_customer(
            customer_id, name="  Renamed  ", phone="  01 555 030  "
        )
        customer = customers.get_customer(customer_id)
        self.assertEqual(customer["name"], "Renamed")
        self.assertEqual(customer["phone"], "01 555 030")


class ListingTests(DatabaseTestCase):
    opens_shift = False

    def test_search_matches_name_phone_and_email(self):
        customers.create_customer(name="Aida Khoury", phone="01555030", email="a@k.com")
        customers.create_customer(name="Bilal Nasr", phone="03222111", email="b@n.com")
        self.assertEqual(len(customers.list_customers(search="khoury")), 1)
        self.assertEqual(len(customers.list_customers(search="03222")), 1)
        self.assertEqual(len(customers.list_customers(search="b@n")), 1)
        self.assertEqual(len(customers.list_customers(search="nobody")), 0)

    def test_owing_only_shows_debtors_and_nobody_else(self):
        owes = customers.create_customer(name="Owes")
        settled = customers.create_customer(name="Settled")
        customers.create_customer(name="Never bought anything")
        accounts.adjust(owes, 40, reason="On account")
        accounts.adjust(settled, 40, reason="On account")
        accounts.adjust(settled, -40, reason="Paid up")

        names = [row["name"] for row in customers.list_customers(owing_only=True)]
        self.assertEqual(names, ["Owes"])

    def test_a_balance_of_rounding_dust_does_not_count_as_owing(self):
        """The filter uses a half-cent floor rather than "> 0".

        Money is stored as a float at the database boundary, so a settled
        account can land a hair off zero. Listing that customer as a debtor
        would send someone to ask them for a fifth of a cent.

        Written straight to the ledger because the service layer will not
        produce it: adjust() rounds to cents and refuses the zero it becomes.
        Drift arrives from the float column, not from anyone typing it.
        """
        customer_id = customers.create_customer(name="Rounding Dust")
        db.execute(
            "INSERT INTO customer_ledger (customer_id, kind, amount_usd) "
            "VALUES (?, 'Adjustment', ?)",
            (customer_id, 0.002),
        )
        self.assertEqual(customers.list_customers(owing_only=True), [])
        # Still not zero, so it must not be quietly deletable either.
        with self.assertRaises(customers.CustomerError):
            customers.delete_customer(customer_id)

    def test_a_long_list_stops_at_the_cap_and_says_so(self):
        for number in range(12):
            customers.create_customer(name=f"Customer {number:02d}")
        rows = customers.list_customers(limit=5)
        self.assertEqual(len(rows), 5)
        self.assertTrue(rows.truncated)

    def test_the_cap_can_be_lifted_for_exports(self):
        for number in range(12):
            customers.create_customer(name=f"Customer {number:02d}")
        rows = customers.list_customers(limit=None)
        self.assertEqual(len(rows), 12)
        self.assertFalse(rows.truncated)


class DeletionTests(DatabaseTestCase):
    """Deleting a customer must never be how a debt disappears."""

    def _sell_to(self, customer_id, amount="25"):
        product_id = products_service.create_product(
            sku="DEL-1", name="Thing", price_usd=amount, cost_usd="1", stock_qty=10
        )
        cart = sales_service.Cart()
        cart.add_product(products_service.get_product(product_id), 1)
        return sales_service.create_sale(
            user_id=self.admin.user_id, cart=cart, amount_paid=100,
            customer_id=customer_id,
        )

    def test_a_settled_customer_is_deleted(self):
        customer_id = customers.create_customer(name="All square")
        customers.delete_customer(customer_id)
        self.assertIsNone(customers.get_customer(customer_id))

    def test_deleting_one_who_owes_money_is_refused(self):
        """customer_ledger cascades, so this used to erase the debt in silence.

        The customer went, their ledger rows went with them, and the shop's
        receivable total dropped by what it was owed with nothing recording
        that anyone had decided to write it off.
        """
        customer_id = customers.create_customer(name="Owes Money")
        accounts.adjust(customer_id, 500, reason="Goods on account")

        with self.assertRaises(customers.CustomerError) as caught:
            customers.delete_customer(customer_id)
        self.assertIn("500", str(caught.exception))

        self.assertIsNotNone(customers.get_customer(customer_id))
        self.assertEqual(accounts.total_receivable(), 500)

    def test_deleting_one_the_shop_owes_is_refused_too(self):
        """A credit is the customer's money sitting with the shop."""
        customer_id = customers.create_customer(name="In Credit")
        accounts.adjust(customer_id, -30, reason="Overpaid")
        with self.assertRaises(customers.CustomerError) as caught:
            customers.delete_customer(customer_id)
        self.assertIn("is owed", str(caught.exception))

    def test_settling_the_account_makes_deletion_possible_again(self):
        customer_id = customers.create_customer(name="Paid Up Eventually")
        accounts.adjust(customer_id, 75, reason="On account")
        accounts.adjust(customer_id, -75, reason="Settled in full")
        customers.delete_customer(customer_id)
        self.assertIsNone(customers.get_customer(customer_id))

    def test_a_layaway_still_held_blocks_deletion(self):
        """Goods set aside against a deposit, which would lose their owner."""
        customer_id = customers.create_customer(name="Holding Something")
        product_id = products_service.create_product(
            sku="LAY-1", name="Held thing", price_usd="60", cost_usd="20", stock_qty=5
        )
        cart = sales_service.Cart()
        cart.add_product(products_service.get_product(product_id), 1)
        layaways.hold(
            cart=cart, customer_id=customer_id, deposit=20, user_id=self.admin.user_id
        )
        with self.assertRaises(customers.CustomerError) as caught:
            customers.delete_customer(customer_id)
        self.assertIn("layaway", str(caught.exception).lower())

    def test_past_invoices_survive_and_become_walk_in_sales(self):
        """The one promise the old docstring made, and it was true."""
        customer_id = customers.create_customer(name="Bought Once")
        sale_id = self._sell_to(customer_id)
        customers.delete_customer(customer_id)

        sale = db.query_one("SELECT * FROM sales WHERE sale_id = ?", (sale_id,))
        self.assertIsNotNone(sale, "the invoice was deleted along with the customer")
        self.assertIsNone(sale["customer_id"])

    def test_deleting_someone_who_is_not_there_says_so(self):
        with self.assertRaises(customers.CustomerError):
            customers.delete_customer(9999)


class HistoryTests(DatabaseTestCase):
    def test_purchase_history_is_newest_first_and_bounded(self):
        customer_id = customers.create_customer(name="Regular")
        product_id = products_service.create_product(
            sku="H-1", name="Thing", price_usd="5", cost_usd="1", stock_qty=100
        )
        for _ in range(3):
            cart = sales_service.Cart()
            cart.add_product(products_service.get_product(product_id), 1)
            sales_service.create_sale(
                user_id=self.admin.user_id, cart=cart, amount_paid=50,
                customer_id=customer_id,
            )

        history = customers.purchase_history(customer_id)
        self.assertEqual(len(history), 3)
        self.assertEqual(
            [row["sale_id"] for row in history],
            sorted((row["sale_id"] for row in history), reverse=True),
        )
        self.assertEqual(len(customers.purchase_history(customer_id, limit=2)), 2)

    def test_someone_who_has_bought_nothing_has_an_empty_history(self):
        customer_id = customers.create_customer(name="Browser")
        self.assertEqual(customers.purchase_history(customer_id), [])


if __name__ == "__main__":
    unittest.main()
