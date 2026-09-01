"""Receipts, return slips and till reports.

The PDF streams are compressed, so content is asserted against the rows the
builder produces and the rendering is checked by the file it writes.
"""

from __future__ import annotations

import unittest

from reportlab.pdfbase.pdfmetrics import stringWidth

from app import receipts as service
from app.services import products as products_service
from app.services import returns as returns_service
from app.services import sales as sales_service
from app.services import settings as settings_service
from app.services import shifts as shifts_service
from tests.support import DatabaseTestCase


def text_of(builder) -> str:
    return "\n".join(f"{left} {right}" for _, left, right in builder.rows)


class ReceiptTestCase(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        self.product = products_service.get_product(
            products_service.create_product(
                sku="S1", name="Widget", price_usd="10.00", cost_usd="4.00", stock_qty=20
            )
        )
        self.sale_id = self.sell(3)

    def sell(self, qty=3, **kwargs):
        cart = sales_service.Cart()
        cart.add_product(self.product, qty)
        kwargs.setdefault("amount_paid", 500)
        return sales_service.create_sale(user_id=self.admin.user_id, cart=cart, **kwargs)

    def rows_for_sale(self, width_mm=80):
        sale = sales_service.get_sale(self.sale_id)
        return service.build_sale(
            sale, sales_service.get_sale_items(self.sale_id),
            settings_service.store_info(),
            settings_service.exchange_rate(), settings_service.lbp_rounding(),
            service.layout_for(width_mm),
        )


class WidthTests(ReceiptTestCase):
    def test_the_configured_width_is_used(self):
        settings_service.set_value("receipt_width_mm", "58")
        self.assertEqual(service.layout_for().width_mm, 58)
        settings_service.set_value("receipt_width_mm", "80")
        self.assertEqual(service.layout_for().width_mm, 80)

    def test_an_unrecognised_width_falls_back_to_80(self):
        settings_service.set_value("receipt_width_mm", "banana")
        self.assertEqual(service.layout_for().width_mm, 80)

    def test_a_narrow_roll_produces_a_narrower_page(self):
        wide = service.layout_for(80)
        narrow = service.layout_for(58)
        self.assertLess(narrow.page_width, wide.page_width)
        self.assertLess(narrow.content_width, wide.content_width)
        self.assertLess(narrow.font("normal")[1], wide.font("normal")[1])

    def test_both_widths_render(self):
        for width in (58, 80):
            path = service.generate_receipt(
                self.sale_id, path=self._tmp / f"r{width}.pdf", width_mm=width
            )
            self.assertTrue(path.exists())
            self.assertGreater(path.stat().st_size, 500)

    def test_nothing_overflows_the_narrow_roll(self):
        layout = service.layout_for(58)
        builder = self.rows_for_sale(width_mm=58)
        for style, left, right in builder.rows:
            if style in ("rule", "gap"):
                continue
            font, size = layout.font(style)
            width = stringWidth(left, font, size) + stringWidth(right, font, size)
            self.assertLessEqual(
                width, layout.content_width + 0.01,
                f"{left!r} {right!r} overflows a 58 mm roll",
            )


class SaleReceiptTests(ReceiptTestCase):
    def test_the_receipt_shows_the_invoice_and_totals(self):
        text = text_of(self.rows_for_sale())
        self.assertIn("INVOICE", text)
        self.assertIn("Widget", text)
        self.assertIn("TOTAL USD", text)
        self.assertIn("TOTAL LBP", text)

    def test_the_rate_stored_on_the_sale_is_used_not_the_current_one(self):
        settings_service.set_value("exchange_rate", "95000")
        later = self.sell(1)
        settings_service.set_value("exchange_rate", "150000")

        sale = sales_service.get_sale(later)
        builder = service.build_sale(
            sale, sales_service.get_sale_items(later), settings_service.store_info(),
            service.D(sale["exchange_rate"]), settings_service.lbp_rounding(),
            service.layout_for(80),
        )
        self.assertIn("95,000 LBP", text_of(builder))

    def test_a_partly_returned_invoice_says_so(self):
        line = returns_service.returnable_lines(self.sale_id)[0]["sale_item_id"]
        returns_service.create_return(self.sale_id, self.admin.user_id, {line: 1})
        text = text_of(self.rows_for_sale())
        self.assertIn("PART RETURNED", text)
        self.assertIn("1 returned", text)

    def test_an_unknown_sale_is_refused(self):
        with self.assertRaises(service.ReceiptError):
            service.generate_receipt(9999, path=self._tmp / "x.pdf")


class ReturnSlipTests(ReceiptTestCase):
    def setUp(self):
        super().setUp()
        line = returns_service.returnable_lines(self.sale_id)[0]["sale_item_id"]
        self.return_id = returns_service.create_return(
            self.sale_id, self.admin.user_id, {line: 2}, reason="Faulty"
        )

    def test_the_slip_is_written(self):
        path = service.generate_return_receipt(
            self.return_id, path=self._tmp / "ret.pdf"
        )
        self.assertTrue(path.exists())

    def test_the_slip_shows_the_refund_and_the_original_invoice(self):
        record = returns_service.get_return(self.return_id)
        builder = service.build_return(
            record, returns_service.get_return_items(self.return_id),
            settings_service.store_info(), service.D(record["exchange_rate"]),
            settings_service.lbp_rounding(), service.layout_for(80),
        )
        text = text_of(builder)
        self.assertIn("RETURN / REFUND", text)
        self.assertIn("REFUND USD", text)
        self.assertIn("Faulty", text)
        self.assertIn(record["invoice_no"], text)
        self.assertIn("$20.00", text)  # two units at 10.00

    def test_an_unknown_return_is_refused(self):
        with self.assertRaises(service.ReceiptError):
            service.generate_return_receipt(9999, path=self._tmp / "x.pdf")


class ShiftReportTests(ReceiptTestCase):
    def report_text(self, kind="Z"):
        shift = shifts_service.get_shift(self.shift_id)
        builder = service.build_shift_report(
            shift, shifts_service.totals(self.shift_id),
            settings_service.store_info(), service.D(shift["exchange_rate"]),
            settings_service.lbp_rounding(), kind, service.layout_for(80),
        )
        return text_of(builder)

    def test_an_x_report_can_be_taken_mid_shift(self):
        text = self.report_text("X")
        self.assertIn("X REPORT", text)
        self.assertIn("Still open", text)
        self.assertIn("not yet counted", text)

    def test_the_report_shows_sales_and_the_expected_drawer(self):
        text = self.report_text("X")
        self.assertIn("Opening float", text)
        self.assertIn("$100.00", text)   # the float
        self.assertIn("Cash sales", text)
        self.assertIn("$30.00", text)    # three widgets at 10.00
        self.assertIn("EXPECTED", text)
        self.assertIn("$130.00", text)

    def test_cash_movements_are_itemised(self):
        shifts_service.add_cash_movement(
            self.shift_id, self.admin.user_id, "Out", "20.00", "Bank drop"
        )
        text = self.report_text("X")
        self.assertIn("Bank drop", text)
        self.assertIn("Paid out", text)

    def test_a_z_report_shows_the_variance(self):
        shifts_service.close_shift(
            self.shift_id, self.admin.user_id, counted_usd="125.00"
        )
        text = self.report_text("Z")
        self.assertIn("Z REPORT", text)
        self.assertIn("Counted USD", text)
        self.assertIn("SHORT", text)
        self.assertIn("$5.00", text)

    def test_a_balanced_drawer_is_labelled_balanced(self):
        shifts_service.close_shift(
            self.shift_id, self.admin.user_id, counted_usd="130.00"
        )
        self.assertIn("BALANCED", self.report_text("Z"))

    def test_the_report_renders_for_both_kinds(self):
        for kind in ("X", "Z"):
            path = service.generate_shift_report(
                self.shift_id, path=self._tmp / f"{kind}.pdf", kind=kind
            )
            self.assertTrue(path.exists())

    def test_an_unknown_shift_is_refused(self):
        with self.assertRaises(service.ReceiptError):
            service.generate_shift_report(9999, path=self._tmp / "x.pdf")


if __name__ == "__main__":
    unittest.main()
