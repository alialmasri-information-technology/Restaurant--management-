"""The end-of-day sheet.

This module composes rather than computes, so the tests here are mostly about
the joins between things that already work: that a day is bounded by its date
and not by a shift, that an uncounted drawer is reported as uncounted rather
than as balanced, and that the receivable printed on an old sheet is the one
that stood at the end of *that* day.
"""

from __future__ import annotations

import datetime as dt

from app import db, receipts
from app.services import accounts as accounts_service
from app.services import customers as customers_service
from app.services import dayend
from app.services import products as products_service
from app.services import returns as returns_service
from app.services import sales as sales_service
from app.services import settings as settings_service
from app.services import shifts as shifts_service
from app.services import stocktake as stocktake_service
from tests.support import DatabaseTestCase

TODAY = dt.date.today().isoformat()
YESTERDAY = (dt.date.today() - dt.timedelta(days=1)).isoformat()


class DayEndTestCase(DatabaseTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.product = products_service.create_product(
            sku="D-1", name="Day Widget", price_usd=20, cost_usd=8, stock_qty=100
        )

    def sell(self, qty=1, method="Cash", customer_id=None):
        cart = sales_service.Cart()
        cart.add_product(products_service.get_product(self.product), qty)
        total = cart.totals()[3]
        return sales_service.create_sale(
            user_id=self.admin.user_id, cart=cart,
            payment_method=method, customer_id=customer_id,
            amount_paid=total if method == "Cash" else 0,
        )


class DateHandling(DayEndTestCase):
    def test_no_argument_means_today(self):
        self.assertEqual(dayend.day_summary()["date"], TODAY)

    def test_a_date_object_is_accepted(self):
        self.assertEqual(dayend.day_summary(dt.date.today())["date"], TODAY)

    def test_a_datetime_is_narrowed_to_its_date(self):
        self.assertEqual(dayend.day_summary(dt.datetime.now())["date"], TODAY)

    def test_a_timestamp_string_is_narrowed_to_its_date(self):
        self.assertEqual(dayend.day_summary(f"{TODAY} 17:45:00")["date"], TODAY)

    def test_a_quiet_day_reports_zeroes_rather_than_failing(self):
        summary = dayend.day_summary("2020-01-01")
        self.assertEqual(summary["sale_count"], 0)
        self.assertEqual(summary["revenue"], 0)
        self.assertEqual(summary["shifts"], [])
        self.assertEqual(summary["margin"], 0.0)


class Trading(DayEndTestCase):
    def test_sales_are_counted_and_totalled(self):
        self.sell(2)
        self.sell(1)
        summary = dayend.day_summary()
        self.assertEqual(summary["sale_count"], 2)
        self.assertEqual(summary["gross_revenue"], 60)
        self.assertEqual(summary["units"], 3)

    def test_returns_are_netted_off_not_hidden(self):
        sale_id = self.sell(2)
        item = db.query_one("SELECT sale_item_id FROM sale_items WHERE sale_id = ?", (sale_id,))
        returns_service.create_return(
            sale_id=sale_id, quantities={item["sale_item_id"]: 1},
            user_id=self.admin.user_id, refund_method="Cash",
        )
        summary = dayend.day_summary()
        self.assertEqual(summary["gross_revenue"], 40)
        self.assertEqual(summary["refund_count"], 1)
        self.assertEqual(summary["revenue"], 20)

    def test_margin_is_a_percentage_of_net_revenue(self):
        self.sell(1)  # 20.00 sold, 8.00 cost
        summary = dayend.day_summary()
        self.assertAlmostEqual(summary["gross_profit"], 12.0, places=2)
        self.assertAlmostEqual(summary["margin"], 60.0, places=1)

    def test_margin_is_zero_rather_than_a_division_error_with_no_revenue(self):
        self.assertEqual(dayend.day_summary()["margin"], 0.0)

    def test_the_payment_mix_is_broken_out(self):
        self.sell(1, method="Cash")
        self.sell(1, method="Card")
        mix = {row["payment_method"]: row["revenue"] for row in dayend.day_summary()["payment_mix"]}
        self.assertEqual(mix, {"Cash": 20, "Card": 20})

    def test_best_sellers_are_capped_at_five(self):
        for index in range(7):
            product = products_service.create_product(
                sku=f"TOP-{index}", name=f"Top {index}", price_usd=5 + index, stock_qty=10
            )
            cart = sales_service.Cart()
            cart.add_product(products_service.get_product(product), 1)
            sales_service.create_sale(
                user_id=self.admin.user_id, cart=cart,
                payment_method="Cash", amount_paid=100,
            )
        self.assertEqual(len(dayend.day_summary()["top_products"]), 5)

    def test_who_served_is_reported(self):
        self.sell(1)
        by_user = dayend.day_summary()["by_user"]
        self.assertEqual(len(by_user), 1)
        self.assertEqual(by_user[0]["sale_count"], 1)


class Drawers(DayEndTestCase):
    def test_the_open_shift_is_listed(self):
        summary = dayend.day_summary()
        self.assertEqual(summary["shift_count"], 1)
        self.assertEqual(summary["open_shifts"], [self.shift_id])

    def test_an_open_drawer_has_no_variance_rather_than_a_zero_one(self):
        shift = dayend.day_summary()["shifts"][0]
        self.assertTrue(shift["is_open"])
        self.assertIsNone(shift["variance_usd"])

    def test_an_open_drawer_is_excluded_from_the_counted_total(self):
        self.sell(1)
        summary = dayend.day_summary()
        self.assertEqual(summary["counted_usd"], 0)
        self.assertEqual(summary["variance_usd"], 0)
        self.assertEqual(summary["expected_usd"], 120)  # 100 float + 20 cash sale

    def test_a_closed_drawer_carries_its_variance(self):
        self.sell(1)
        shifts_service.close_shift(self.shift_id, self.admin.user_id, counted_usd=125)
        summary = dayend.day_summary()
        self.assertEqual(summary["counted_usd"], 125)
        self.assertAlmostEqual(summary["variance_usd"], 5.0, places=2)
        self.assertTrue(summary["reconciled"])

    def test_variances_across_several_drawers_add_up(self):
        shifts_service.close_shift(self.shift_id, self.admin.user_id, counted_usd=98)
        second = shifts_service.open_shift(self.admin.user_id, opening_float=50)
        shifts_service.close_shift(second, self.admin.user_id, counted_usd=53)
        summary = dayend.day_summary()
        self.assertEqual(summary["shift_count"], 2)
        self.assertAlmostEqual(summary["variance_usd"], 1.0, places=2)

    def test_the_three_cash_figures_always_add_up(self):
        """Expected, counted and variance are printed as a column.

        They were computed over different sets: expected covered every drawer,
        counted and variance only the ones that had been counted. So the
        moment a till was left open the sheet read 300 expected, 150 counted
        and a variance of 0.00 underneath calling itself BALANCED -- 150
        apparently missing, next to the word for nothing missing.
        """
        self.sell(1)
        shifts_service.close_shift(self.shift_id, self.admin.user_id, counted_usd=120)
        second = shifts_service.open_shift(self.admin.user_id, opening_float=100)
        self.sell(1)

        summary = dayend.day_summary()
        self.assertIn(second, summary["open_shifts"])
        # variance is counted - expected, so short reads negative.
        self.assertAlmostEqual(
            summary["counted_usd"] - summary["expected_counted_usd"],
            summary["variance_usd"], places=2,
        )

    def test_money_in_an_open_till_is_named_rather_than_dropped(self):
        self.sell(1)
        shifts_service.close_shift(self.shift_id, self.admin.user_id, counted_usd=120)
        shifts_service.open_shift(self.admin.user_id, opening_float=100)
        self.sell(1)

        summary = dayend.day_summary()
        self.assertEqual(summary["expected_counted_usd"], 120)
        self.assertEqual(summary["expected_open_usd"], 120)  # 100 float + 20 sale
        self.assertEqual(
            summary["expected_usd"],
            summary["expected_counted_usd"] + summary["expected_open_usd"],
            "the whole day is still the sum of both",
        )

    def test_with_every_till_closed_the_two_expected_figures_agree(self):
        """The ordinary day must read exactly as it did before."""
        self.sell(1)
        shifts_service.close_shift(self.shift_id, self.admin.user_id, counted_usd=120)
        summary = dayend.day_summary()
        self.assertEqual(summary["expected_counted_usd"], summary["expected_usd"])
        self.assertEqual(summary["expected_open_usd"], 0)

    def test_a_shift_from_another_day_is_not_included(self):
        db.execute(
            "UPDATE shifts SET opened_at = ? WHERE shift_id = ?",
            (f"{YESTERDAY} 09:00:00", self.shift_id),
        )
        self.assertEqual(dayend.day_summary()["shift_count"], 0)
        self.assertEqual(dayend.day_summary(YESTERDAY)["shift_count"], 1)


class Warnings(DayEndTestCase):
    def test_an_open_till_is_called_out(self):
        notes = dayend.warnings(dayend.day_summary())
        self.assertTrue(any("still open" in note for note in notes))

    def test_a_balanced_closed_day_has_nothing_to_say(self):
        shifts_service.close_shift(self.shift_id, self.admin.user_id, counted_usd=100)
        self.assertEqual(dayend.warnings(dayend.day_summary()), [])

    def test_a_short_drawer_is_described_as_short(self):
        shifts_service.close_shift(self.shift_id, self.admin.user_id, counted_usd=90)
        notes = dayend.warnings(dayend.day_summary())
        self.assertTrue(any("short by" in note for note in notes), notes)

    def test_a_surplus_is_described_as_over(self):
        shifts_service.close_shift(self.shift_id, self.admin.user_id, counted_usd=110)
        notes = dayend.warnings(dayend.day_summary())
        self.assertTrue(any("over by" in note for note in notes), notes)

    def test_a_cent_of_rounding_is_not_a_warning(self):
        shifts_service.close_shift(self.shift_id, self.admin.user_id, counted_usd=100.01)
        self.assertFalse(dayend.unbalanced(dayend.day_summary()))


class NoShiftDay(DayEndTestCase):
    opens_shift = False

    def test_selling_with_no_till_open_is_flagged(self):
        settings_service.set_value("require_shift", "0")
        self.sell(1)
        summary = dayend.day_summary()
        self.assertEqual(summary["shift_count"], 0)
        notes = dayend.warnings(summary)
        self.assertTrue(any("no till shift" in note for note in notes), notes)


class Accounts(DayEndTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.customer = customers_service.create_customer(name="Cafe Nadia")
        accounts_service.set_credit_limit(self.customer, 500)

    def test_what_went_on_account_today_is_reported(self):
        self.sell(2, method="Credit", customer_id=self.customer)
        account = dayend.day_summary()["account"]
        self.assertEqual(account["charged"], 40)
        self.assertEqual(account["closing_receivable"], 40)

    def test_a_payment_is_split_out_by_whether_it_was_cash(self):
        self.sell(2, method="Credit", customer_id=self.customer)
        accounts_service.record_payment(self.customer, 10, user_id=self.admin.user_id, method="Cash")
        accounts_service.record_payment(self.customer, 5, user_id=self.admin.user_id, method="Card")
        account = dayend.day_summary()["account"]
        self.assertEqual(account["collected"], 15)
        self.assertEqual(account["collected_cash"], 10)
        self.assertEqual(account["closing_receivable"], 25)

    def test_cash_taken_on_account_reaches_the_expected_drawer_figure(self):
        self.sell(2, method="Credit", customer_id=self.customer)
        accounts_service.record_payment(self.customer, 10, user_id=self.admin.user_id, method="Cash")
        # 100 float + 10 collected; the credit sale itself put nothing in.
        self.assertEqual(dayend.day_summary()["expected_usd"], 110)

    def test_an_adjustment_is_reported_separately(self):
        self.sell(2, method="Credit", customer_id=self.customer)
        accounts_service.adjust(self.customer, -40, user_id=self.admin.user_id, reason="Written off")
        account = dayend.day_summary()["account"]
        self.assertEqual(account["adjusted"], -40)
        self.assertEqual(account["closing_receivable"], 0)

    def test_the_receivable_is_the_one_that_stood_at_the_end_of_that_day(self):
        self.sell(2, method="Credit", customer_id=self.customer)
        db.execute("UPDATE customer_ledger SET at = ?", (f"{YESTERDAY} 12:00:00",))
        accounts_service.record_payment(self.customer, 40, user_id=self.admin.user_id, method="Cash")
        # Yesterday closed owing 40 even though it has since been paid off.
        self.assertEqual(dayend.day_summary(YESTERDAY)["account"]["closing_receivable"], 40)
        self.assertEqual(dayend.day_summary()["account"]["closing_receivable"], 0)


class StockTakes(DayEndTestCase):
    def test_a_posted_count_appears_with_its_variance_at_cost(self):
        take_id = stocktake_service.open_count(self.admin.user_id)
        stocktake_service.record_count(take_id, self.product, 97)
        stocktake_service.apply_count(take_id, self.admin.user_id)
        takes = dayend.day_summary()["stock_takes"]
        self.assertEqual(len(takes), 1)
        self.assertEqual(takes[0]["variance_units"], -3)
        self.assertAlmostEqual(takes[0]["variance_usd"], -24.0, places=2)

    def test_an_open_count_is_not_reported_as_posted(self):
        stocktake_service.open_count(self.admin.user_id)
        self.assertEqual(dayend.day_summary()["stock_takes"], [])

    def test_an_abandoned_count_is_not_reported(self):
        take_id = stocktake_service.open_count(self.admin.user_id)
        stocktake_service.cancel_count(take_id, self.admin.user_id)
        self.assertEqual(dayend.day_summary()["stock_takes"], [])


class Sheet(DayEndTestCase):
    """The PDF itself. Nothing asserts what it looks like — only that every
    branch of it renders, because a report that throws at close of business is
    worse than one that reads awkwardly."""

    def render(self, date=None):
        return receipts.generate_day_report(date)

    def test_a_quiet_day_still_produces_a_sheet(self):
        path = self.render("2020-01-01")
        self.assertTrue(path.exists())
        self.assertGreater(path.stat().st_size, 500)

    def test_a_full_day_renders(self):
        customer = customers_service.create_customer(name="Cafe Nadia")
        accounts_service.set_credit_limit(customer, 500)
        sale_id = self.sell(2)
        self.sell(1, method="Card")
        self.sell(1, method="Credit", customer_id=customer)
        accounts_service.record_payment(customer, 5, user_id=self.admin.user_id, method="Cash")
        item = db.query_one("SELECT sale_item_id FROM sale_items WHERE sale_id = ?", (sale_id,))
        returns_service.create_return(
            sale_id=sale_id, quantities={item["sale_item_id"]: 1},
            user_id=self.admin.user_id, refund_method="Cash",
        )
        take_id = stocktake_service.open_count(self.admin.user_id)
        stocktake_service.record_count(take_id, self.product, 90)
        stocktake_service.apply_count(take_id, self.admin.user_id)
        shifts_service.close_shift(self.shift_id, self.admin.user_id, counted_usd=118)

        path = self.render()
        self.assertTrue(path.exists())
        self.assertGreater(path.stat().st_size, 1000)

    def test_it_renders_on_a_narrow_roll_too(self):
        self.sell(1)
        path = receipts.generate_day_report(None, width_mm=58)
        self.assertTrue(path.exists())

    def test_the_file_is_named_for_its_day(self):
        self.assertIn("2020-01-01", self.render("2020-01-01").name)
