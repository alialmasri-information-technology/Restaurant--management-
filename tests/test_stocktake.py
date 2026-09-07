"""Stock takes: freezing a worksheet, counting it, and posting the variance."""

from __future__ import annotations

from app import config, db
from app.services import audit as audit_service
from app.services import products as products_service
from app.services import sales as sales_service
from app.services import stocktake
from tests.support import DatabaseTestCase


class StockTakeTestCase(DatabaseTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.widget = products_service.create_product(
            sku="W-1", name="Widget", price_usd=10, cost_usd=4, stock_qty=20
        )
        self.gadget = products_service.create_product(
            sku="G-1", name="Gadget", price_usd=6, cost_usd=2, stock_qty=8,
            barcode="5001",
        )

    def stock(self, product_id: int) -> int:
        return db.scalar(
            "SELECT stock_qty FROM products WHERE product_id = ?", (product_id,)
        )


class OpeningTests(StockTakeTestCase):
    def test_opening_freezes_a_line_for_every_product_in_scope(self):
        take_id = stocktake.open_count(self.admin.user_id)
        items = stocktake.list_items(take_id)
        self.assertEqual({row["name_at_count"] for row in items}, {"Widget", "Gadget"})
        self.assertEqual({row["expected_qty"] for row in items}, {20, 8})
        self.assertTrue(all(row["counted_qty"] is None for row in items))

    def test_the_expected_quantity_does_not_follow_later_sales(self):
        take_id = stocktake.open_count(self.admin.user_id)
        cart = sales_service.Cart()
        cart.add_product(products_service.get_product(self.widget), 5)
        sales_service.create_sale(user_id=self.admin.user_id, cart=cart, amount_paid=100)

        line = next(
            row for row in stocktake.list_items(take_id) if row["name_at_count"] == "Widget"
        )
        self.assertEqual(line["expected_qty"], 20)
        self.assertEqual(self.stock(self.widget), 15)

    def test_only_one_count_may_be_open_at_a_time(self):
        stocktake.open_count(self.admin.user_id)
        with self.assertRaises(stocktake.StockTakeError):
            stocktake.open_count(self.admin.user_id)

    def test_a_count_can_be_scoped_to_one_category(self):
        category_id = products_service.create_category("Tools")
        products_service.update_product(
            self.widget, sku="W-1", name="Widget", price_usd=10, cost_usd=4,
            category_id=category_id,
        )
        take_id = stocktake.open_count(self.admin.user_id, category_id=category_id)
        items = stocktake.list_items(take_id)
        self.assertEqual([row["name_at_count"] for row in items], ["Widget"])
        self.assertIn("Tools", stocktake.get_count(take_id)["scope"])

    def test_products_with_no_stock_are_off_the_sheet_unless_asked_for(self):
        empty = products_service.create_product(
            sku="E-1", name="Empty shelf", price_usd=1, stock_qty=0
        )
        without = stocktake.open_count(self.admin.user_id)
        self.assertNotIn(
            empty, [row["product_id"] for row in stocktake.list_items(without)]
        )
        stocktake.cancel_count(without, self.admin.user_id)

        with_zero = stocktake.open_count(self.admin.user_id, include_zero_stock=True)
        self.assertIn(
            empty, [row["product_id"] for row in stocktake.list_items(with_zero)]
        )

    def test_references_are_sequential_within_the_day(self):
        first = stocktake.open_count(self.admin.user_id)
        stocktake.cancel_count(first, self.admin.user_id)
        second = stocktake.open_count(self.admin.user_id)
        self.assertLess(
            stocktake.get_count(first)["reference"],
            stocktake.get_count(second)["reference"],
        )

    def test_an_empty_scope_is_refused(self):
        category_id = products_service.create_category("Nothing here")
        with self.assertRaises(stocktake.StockTakeError):
            stocktake.open_count(self.admin.user_id, category_id=category_id)


class CountingTests(StockTakeTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.take_id = stocktake.open_count(self.admin.user_id)

    def test_recording_a_count_leaves_stock_alone(self):
        stocktake.record_count(self.take_id, self.widget, 17)
        self.assertEqual(self.stock(self.widget), 20)

    def test_scanning_accumulates_from_zero_not_from_the_expected_figure(self):
        stocktake.scan(self.take_id, "5001")
        stocktake.scan(self.take_id, "5001")
        line = next(
            row for row in stocktake.list_items(self.take_id)
            if row["product_id"] == self.gadget
        )
        self.assertEqual(line["counted_qty"], 2)

    def test_scanning_an_unknown_code_is_refused(self):
        with self.assertRaises(stocktake.StockTakeError):
            stocktake.scan(self.take_id, "not-a-code")

    def test_a_line_can_be_put_back_to_uncounted(self):
        stocktake.record_count(self.take_id, self.widget, 3)
        stocktake.clear_line(self.take_id, self.widget)
        line = next(
            row for row in stocktake.list_items(self.take_id)
            if row["product_id"] == self.widget
        )
        self.assertIsNone(line["counted_qty"])

    def test_a_negative_count_is_refused(self):
        with self.assertRaises(stocktake.StockTakeError):
            stocktake.record_count(self.take_id, self.widget, -1)

    def test_counting_a_product_that_is_not_on_the_sheet_is_refused(self):
        other = products_service.create_product(
            sku="X-1", name="Added later", price_usd=1, stock_qty=5
        )
        with self.assertRaises(stocktake.StockTakeError):
            stocktake.record_count(self.take_id, other, 5)

    def test_remaining_lines_can_be_accepted_as_they_stand(self):
        stocktake.record_count(self.take_id, self.widget, 18)
        filled = stocktake.count_remaining_as_expected(self.take_id)
        self.assertEqual(filled, 1)
        totals = stocktake.summary(self.take_id)
        self.assertEqual(totals["uncounted_lines"], 0)
        self.assertEqual(totals["variance_lines"], 1)

    def test_the_summary_separates_shortage_from_surplus(self):
        stocktake.record_count(self.take_id, self.widget, 18)  # 2 short, $4 cost each
        stocktake.record_count(self.take_id, self.gadget, 9)   # 1 over, $2 cost each
        totals = stocktake.summary(self.take_id)
        self.assertEqual(totals["shortage_units"], 2)
        self.assertEqual(totals["surplus_units"], 1)
        self.assertEqual(totals["net_units"], -1)
        self.assertAlmostEqual(totals["net_value"], -6.0, places=2)

    def test_totalling_the_sheet_in_memory_agrees_with_the_database(self):
        """The counting screen adds the sheet up itself; it must not drift.

        Every scan would otherwise ask the database to re-total a worksheet the
        screen is already holding. Two ways of reaching one number is a bug
        waiting to happen, so they are checked against each other here — at a
        half-counted sheet, which is where a disagreement would show.
        """
        stocktake.record_count(self.take_id, self.widget, 18)
        rows = stocktake.list_items(self.take_id)
        self.assertEqual(
            stocktake.summarise(rows), dict(stocktake.summary(self.take_id))
        )

    def test_totalling_an_untouched_and_a_finished_sheet_also_agree(self):
        self.assertEqual(
            stocktake.summarise(stocktake.list_items(self.take_id)),
            dict(stocktake.summary(self.take_id)),
        )
        stocktake.record_count(self.take_id, self.widget, 18)
        stocktake.record_count(self.take_id, self.gadget, 9)
        self.assertEqual(
            stocktake.summarise(stocktake.list_items(self.take_id)),
            dict(stocktake.summary(self.take_id)),
        )

    def test_lines_can_be_filtered_to_the_variances(self):
        stocktake.record_count(self.take_id, self.widget, 20)
        stocktake.record_count(self.take_id, self.gadget, 5)
        rows = stocktake.list_items(self.take_id, only_variances=True)
        self.assertEqual([row["product_id"] for row in rows], [self.gadget])

    def test_lines_can_be_filtered_to_the_uncounted(self):
        stocktake.record_count(self.take_id, self.widget, 20)
        rows = stocktake.list_items(self.take_id, only_uncounted=True)
        self.assertEqual([row["product_id"] for row in rows], [self.gadget])


class ApplyingTests(StockTakeTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.take_id = stocktake.open_count(self.admin.user_id)

    def test_applying_moves_stock_to_the_counted_figure(self):
        stocktake.record_count(self.take_id, self.widget, 17)
        stocktake.apply_count(self.take_id, self.admin.user_id)
        self.assertEqual(self.stock(self.widget), 17)

    def test_uncounted_lines_are_left_alone(self):
        stocktake.record_count(self.take_id, self.widget, 17)
        stocktake.apply_count(self.take_id, self.admin.user_id)
        self.assertEqual(self.stock(self.gadget), 8)

    def test_selling_during_the_count_survives_the_adjustment(self):
        # Counter finds 18 of the 20 the system expected: two are missing.
        stocktake.record_count(self.take_id, self.widget, 18)
        cart = sales_service.Cart()
        cart.add_product(products_service.get_product(self.widget), 5)
        sales_service.create_sale(user_id=self.admin.user_id, cart=cart, amount_paid=100)
        self.assertEqual(self.stock(self.widget), 15)

        stocktake.apply_count(self.take_id, self.admin.user_id)
        # 15 on hand less the 2 that were missing — the five sold are not undone.
        self.assertEqual(self.stock(self.widget), 13)

    def test_an_adjustment_is_written_to_the_stock_history(self):
        stocktake.record_count(self.take_id, self.widget, 15)
        stocktake.apply_count(self.take_id, self.admin.user_id)
        history = products_service.stock_history(self.widget)
        self.assertEqual(history[0]["reason"], "Stock take")
        self.assertEqual(history[0]["change_qty"], -5)
        self.assertEqual(history[0]["new_stock"], 15)

    def test_stock_is_never_driven_below_zero(self):
        stocktake.record_count(self.take_id, self.gadget, 0)
        cart = sales_service.Cart()
        cart.add_product(products_service.get_product(self.gadget), 8)
        sales_service.create_sale(user_id=self.admin.user_id, cart=cart, amount_paid=100)
        self.assertEqual(self.stock(self.gadget), 0)

        result = stocktake.apply_count(self.take_id, self.admin.user_id)
        self.assertEqual(self.stock(self.gadget), 0)
        self.assertIn("Gadget", result["clamped"])

    def test_a_count_with_no_variance_applies_cleanly(self):
        stocktake.record_count(self.take_id, self.widget, 20)
        stocktake.record_count(self.take_id, self.gadget, 8)
        result = stocktake.apply_count(self.take_id, self.admin.user_id)
        self.assertEqual(result["adjustments"], 0)
        self.assertEqual(self.stock(self.widget), 20)
        self.assertEqual(
            stocktake.get_count(self.take_id)["status"], config.TAKE_APPLIED
        )

    def test_applying_nothing_is_refused(self):
        with self.assertRaises(stocktake.StockTakeError):
            stocktake.apply_count(self.take_id, self.admin.user_id)

    def test_an_applied_count_cannot_be_edited_or_applied_again(self):
        stocktake.record_count(self.take_id, self.widget, 19)
        stocktake.apply_count(self.take_id, self.admin.user_id)
        with self.assertRaises(stocktake.StockTakeError):
            stocktake.record_count(self.take_id, self.widget, 5)
        with self.assertRaises(stocktake.StockTakeError):
            stocktake.apply_count(self.take_id, self.admin.user_id)

    def test_cancelling_leaves_stock_untouched(self):
        stocktake.record_count(self.take_id, self.widget, 2)
        stocktake.cancel_count(self.take_id, self.admin.user_id, reason="Miscount")
        self.assertEqual(self.stock(self.widget), 20)
        self.assertEqual(
            stocktake.get_count(self.take_id)["status"], config.TAKE_CANCELLED
        )
        self.assertIsNone(stocktake.current_count())

    def test_the_variance_value_is_reported_at_cost(self):
        stocktake.record_count(self.take_id, self.widget, 18)  # -2 x $4
        stocktake.record_count(self.take_id, self.gadget, 8)
        self.assertEqual(str(stocktake.variance_value(self.take_id)), "-8.00")

    def test_opening_and_applying_are_audited(self):
        stocktake.record_count(self.take_id, self.widget, 18)
        stocktake.apply_count(self.take_id, self.admin.user_id)
        actions = [row["action"] for row in audit_service.list_entries()]
        self.assertIn("Stock take opened", actions)
        self.assertIn("Stock take applied", actions)
