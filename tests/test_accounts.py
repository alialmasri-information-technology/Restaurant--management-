"""Customer accounts: selling on credit, and getting the money back.

The behaviour that matters most here is that a debt and the invoice that created
it are written together or not at all, and that money taken against an account
reaches the till — a payment the drawer holds but the shift does not know about
would show up as an unexplained surplus at close.
"""

from __future__ import annotations

from app import db
from app.services import accounts
from app.services import audit as audit_service
from app.services import customers as customers_service
from app.services import products as products_service
from app.services import returns as returns_service
from app.services import sales as sales_service
from app.services import settings as settings_service
from app.services import shifts as shifts_service
from tests.support import DatabaseTestCase


class AccountTestCase(DatabaseTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.product = products_service.create_product(
            sku="A-1", name="Account Widget", price_usd=25, cost_usd=10, stock_qty=100
        )
        self.customer = customers_service.create_customer(name="Cafe Nadia", phone="01")
        self.walk_in = customers_service.create_customer(name="Passer By")
        accounts.set_credit_limit(self.customer, 500)

    def sell_on_credit(self, qty: int = 2, customer_id: int | None = -1) -> int:
        cart = sales_service.Cart()
        cart.add_product(products_service.get_product(self.product), qty)
        return sales_service.create_sale(
            user_id=self.admin.user_id,
            cart=cart,
            customer_id=self.customer if customer_id == -1 else customer_id,
            payment_method="Credit",
        )


class SellingOnCreditTests(AccountTestCase):
    def test_a_credit_sale_becomes_a_debt(self):
        self.sell_on_credit(2)  # 2 x $25
        self.assertEqual(str(accounts.balance(self.customer)), "50.00")

    def test_nothing_is_recorded_as_paid(self):
        sale_id = self.sell_on_credit(2)
        sale = sales_service.get_sale(sale_id)
        self.assertEqual(sale["amount_paid"], 0)
        self.assertEqual(sale["change_usd"], 0)
        self.assertEqual(sale["total_usd"], 50)

    def test_a_cash_sale_never_touches_the_ledger(self):
        cart = sales_service.Cart()
        cart.add_product(products_service.get_product(self.product), 1)
        sales_service.create_sale(
            user_id=self.admin.user_id, cart=cart,
            customer_id=self.customer, amount_paid=25,
        )
        self.assertEqual(str(accounts.balance(self.customer)), "0.00")

    def test_a_sale_on_account_needs_a_customer(self):
        with self.assertRaises(sales_service.SaleError) as caught:
            self.sell_on_credit(1, customer_id=None)
        self.assertIn("needs a customer", str(caught.exception))

    def test_a_customer_with_no_limit_cannot_buy_on_account(self):
        with self.assertRaises(sales_service.SaleError) as caught:
            self.sell_on_credit(1, customer_id=self.walk_in)
        self.assertIn("no credit limit", str(caught.exception))

    def test_a_sale_over_the_limit_is_refused(self):
        accounts.set_credit_limit(self.customer, 60)
        self.sell_on_credit(2)  # $50, inside the limit
        with self.assertRaises(sales_service.SaleError) as caught:
            self.sell_on_credit(1)  # would reach $75
        self.assertIn("would take them to", str(caught.exception))

    def test_a_refused_sale_writes_nothing_at_all(self):
        accounts.set_credit_limit(self.customer, 10)
        before_stock = products_service.get_product(self.product)["stock_qty"]
        with self.assertRaises(sales_service.SaleError):
            self.sell_on_credit(2)
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM sales", default=0), 0)
        self.assertEqual(
            products_service.get_product(self.product)["stock_qty"], before_stock
        )
        self.assertEqual(str(accounts.balance(self.customer)), "0.00")

    def test_a_sale_exactly_on_the_limit_is_allowed(self):
        accounts.set_credit_limit(self.customer, 50)
        self.sell_on_credit(2)
        self.assertEqual(str(accounts.balance(self.customer)), "50.00")


class PaymentTests(AccountTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.sell_on_credit(4)  # $100

    def test_a_payment_reduces_the_balance(self):
        accounts.record_payment(self.customer, 30, user_id=self.admin.user_id)
        self.assertEqual(str(accounts.balance(self.customer)), "70.00")

    def test_paying_it_all_clears_the_account(self):
        accounts.record_payment(self.customer, 100, user_id=self.admin.user_id)
        self.assertEqual(str(accounts.balance(self.customer)), "0.00")
        self.assertEqual(accounts.outstanding(), [])

    def test_overpaying_is_refused(self):
        with self.assertRaises(accounts.AccountError) as caught:
            accounts.record_payment(self.customer, 150, user_id=self.admin.user_id)
        self.assertIn("owes", str(caught.exception))
        self.assertEqual(str(accounts.balance(self.customer)), "100.00")

    def test_paying_an_account_that_owes_nothing_is_refused(self):
        with self.assertRaises(accounts.AccountError):
            accounts.record_payment(self.walk_in, 10, user_id=self.admin.user_id)

    def test_a_payment_of_zero_or_less_is_refused(self):
        for amount in (0, -5):
            with self.assertRaises(accounts.AccountError):
                accounts.record_payment(self.customer, amount, user_id=self.admin.user_id)

    def test_a_payment_in_lbp_is_converted_at_the_rate(self):
        settings_service.set_value("exchange_rate", "90000")
        accounts.record_payment(
            self.customer, 900_000, paid_currency="LBP", user_id=self.admin.user_id
        )
        self.assertEqual(str(accounts.balance(self.customer)), "90.00")

    def test_a_payment_is_audited_without_touching_the_invoice(self):
        accounts.record_payment(self.customer, 40, user_id=self.admin.user_id)
        actions = [row["action"] for row in audit_service.list_entries()]
        self.assertIn("Account payment", actions)
        self.assertEqual(sales_service.list_sales()[0]["amount_paid"], 0)


class TillTests(AccountTestCase):
    """Money taken on an account still has to reconcile at the end of the day."""

    def test_a_cash_payment_reaches_the_expected_drawer_total(self):
        self.sell_on_credit(4)  # $100 on account, no cash
        before = shifts_service.totals(self.shift_id)["expected_usd"]

        accounts.record_payment(self.customer, 60, user_id=self.admin.user_id)

        after = shifts_service.totals(self.shift_id)
        self.assertAlmostEqual(after["expected_usd"], before + 60, places=2)
        self.assertAlmostEqual(after["cash_account_payments"], 60, places=2)

    def test_a_card_payment_is_counted_but_not_expected_in_the_drawer(self):
        self.sell_on_credit(4)
        before = shifts_service.totals(self.shift_id)["expected_usd"]

        accounts.record_payment(
            self.customer, 60, method="Card", user_id=self.admin.user_id
        )

        after = shifts_service.totals(self.shift_id)
        self.assertAlmostEqual(after["expected_usd"], before, places=2)
        self.assertAlmostEqual(after["account_payments"], 60, places=2)
        self.assertAlmostEqual(after["cash_account_payments"], 0, places=2)

    def test_a_credit_sale_puts_no_cash_in_the_drawer(self):
        before = shifts_service.totals(self.shift_id)["expected_usd"]
        self.sell_on_credit(4)
        self.assertAlmostEqual(
            shifts_service.totals(self.shift_id)["expected_usd"], before, places=2
        )


class ReturnTests(AccountTestCase):
    def test_returning_goods_off_an_account_sale_shrinks_the_debt(self):
        sale_id = self.sell_on_credit(4)  # $100
        line = returns_service.returnable_lines(sale_id)[0]

        returns_service.create_return(
            sale_id, self.admin.user_id, {line["sale_item_id"]: 1},
            refund_method="Credit",
        )
        self.assertEqual(str(accounts.balance(self.customer)), "75.00")

    def test_a_credit_refund_takes_no_cash_out_of_the_drawer(self):
        sale_id = self.sell_on_credit(4)
        before = shifts_service.totals(self.shift_id)["expected_usd"]
        line = returns_service.returnable_lines(sale_id)[0]

        returns_service.create_return(
            sale_id, self.admin.user_id, {line["sale_item_id"]: 2},
            refund_method="Credit",
        )
        self.assertAlmostEqual(
            shifts_service.totals(self.shift_id)["expected_usd"], before, places=2
        )

    def test_crediting_a_sale_with_no_customer_is_refused(self):
        cart = sales_service.Cart()
        cart.add_product(products_service.get_product(self.product), 1)
        sale_id = sales_service.create_sale(
            user_id=self.admin.user_id, cart=cart, amount_paid=25
        )
        line = returns_service.returnable_lines(sale_id)[0]
        with self.assertRaises(returns_service.ReturnError):
            returns_service.create_return(
                sale_id, self.admin.user_id, {line["sale_item_id"]: 1},
                refund_method="Credit",
            )

    def test_a_cash_refund_on_a_credit_sale_leaves_the_debt_alone(self):
        # Deliberate: handing cash back does not settle what they still owe.
        sale_id = self.sell_on_credit(4)
        line = returns_service.returnable_lines(sale_id)[0]
        returns_service.create_return(
            sale_id, self.admin.user_id, {line["sale_item_id"]: 1},
            refund_method="Cash",
        )
        self.assertEqual(str(accounts.balance(self.customer)), "100.00")


class StatementTests(AccountTestCase):
    def test_the_statement_carries_a_running_balance(self):
        self.sell_on_credit(2)  # +50
        accounts.record_payment(self.customer, 20, user_id=self.admin.user_id)  # -20
        self.sell_on_credit(1)  # +25

        rows = accounts.statement(self.customer)
        self.assertEqual([row["kind"] for row in rows], ["Sale", "Payment", "Sale"])
        self.assertEqual(
            [round(row["running_balance"], 2) for row in rows], [50.0, 30.0, 55.0]
        )

    def test_the_statement_names_the_invoice_behind_each_charge(self):
        sale_id = self.sell_on_credit(2)
        invoice_no = sales_service.get_sale(sale_id)["invoice_no"]
        self.assertEqual(accounts.statement(self.customer)[0]["document"], invoice_no)

    def test_outstanding_lists_debtors_biggest_first(self):
        other = customers_service.create_customer(name="Small Debt")
        accounts.set_credit_limit(other, 100)
        self.sell_on_credit(4)  # Cafe Nadia: $100

        cart = sales_service.Cart()
        cart.add_product(products_service.get_product(self.product), 1)
        sales_service.create_sale(
            user_id=self.admin.user_id, cart=cart,
            customer_id=other, payment_method="Credit",
        )

        rows = accounts.outstanding()
        self.assertEqual([row["name"] for row in rows], ["Cafe Nadia", "Small Debt"])

    def test_a_settled_account_drops_off_the_outstanding_list(self):
        self.sell_on_credit(2)
        accounts.record_payment(self.customer, 50, user_id=self.admin.user_id)
        self.assertEqual(accounts.outstanding(), [])

    def test_total_receivable_adds_up_what_is_owed(self):
        other = customers_service.create_customer(name="Also Owes")
        accounts.set_credit_limit(other, 100)
        self.sell_on_credit(2)  # 50

        cart = sales_service.Cart()
        cart.add_product(products_service.get_product(self.product), 1)
        sales_service.create_sale(
            user_id=self.admin.user_id, cart=cart,
            customer_id=other, payment_method="Credit",
        )  # 25
        self.assertEqual(str(accounts.total_receivable()), "75.00")


class AdjustmentTests(AccountTestCase):
    def test_a_debt_can_be_written_off_with_a_reason(self):
        self.sell_on_credit(2)
        accounts.adjust(self.customer, -50, user_id=self.admin.user_id, reason="Goodwill")
        self.assertEqual(str(accounts.balance(self.customer)), "0.00")

    def test_an_adjustment_needs_a_reason(self):
        self.sell_on_credit(2)
        with self.assertRaises(accounts.AccountError):
            accounts.adjust(self.customer, -50, user_id=self.admin.user_id)

    def test_an_adjustment_of_zero_is_refused(self):
        with self.assertRaises(accounts.AccountError):
            accounts.adjust(self.customer, 0, user_id=self.admin.user_id, reason="x")

    def test_an_adjustment_is_audited(self):
        self.sell_on_credit(2)
        accounts.adjust(self.customer, -50, user_id=self.admin.user_id, reason="Goodwill")
        self.assertIn(
            "Account adjusted", [r["action"] for r in audit_service.list_entries()]
        )


class CreditLimitTests(AccountTestCase):
    def test_a_negative_limit_is_refused(self):
        with self.assertRaises(accounts.AccountError):
            accounts.set_credit_limit(self.customer, -1)

    def test_setting_a_limit_is_audited(self):
        accounts.set_credit_limit(self.customer, 250)
        self.assertEqual(str(accounts.credit_limit(self.customer)), "250.00")
        self.assertIn(
            "Credit limit set", [r["action"] for r in audit_service.list_entries()]
        )

    def test_lowering_the_limit_below_what_is_owed_blocks_further_credit(self):
        self.sell_on_credit(4)  # $100
        accounts.set_credit_limit(self.customer, 60)
        with self.assertRaises(sales_service.SaleError):
            self.sell_on_credit(1)
        # The existing debt stands; only new credit is refused.
        self.assertEqual(str(accounts.balance(self.customer)), "100.00")
