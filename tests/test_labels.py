"""Barcode label sheets."""

from __future__ import annotations

import unittest

from reportlab.lib.units import mm

from app import labels as service
from app.services import products as products_service
from tests.support import DatabaseTestCase


class LabelTests(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        self.product_id = products_service.create_product(
            sku="ABC-001", name="Widget", price_usd="10.00", barcode="5901234123457"
        )
        self.no_barcode_id = products_service.create_product(
            sku="XYZ-002", name="Gadget", price_usd="2.50"
        )

    def path(self, name="labels.pdf"):
        return self._tmp / name

    def test_a_sheet_is_written(self):
        path = service.generate_labels({self.product_id: 4}, path=self.path())
        self.assertTrue(path.exists())
        self.assertGreater(path.stat().st_size, 500)

    def test_every_format_renders(self):
        for key in service.SHEETS:
            path = service.generate_labels(
                {self.product_id: 3}, path=self.path(f"{key}.pdf"), sheet=key
            )
            self.assertTrue(path.exists(), key)

    def test_a_product_without_a_barcode_is_labelled_with_its_sku(self):
        product = products_service.get_product(self.no_barcode_id)
        self.assertEqual(service.code_for(product), "XYZ-002")
        service.generate_labels({self.no_barcode_id: 1}, path=self.path())

    def test_the_barcode_is_preferred_over_the_sku(self):
        product = products_service.get_product(self.product_id)
        self.assertEqual(service.code_for(product), "5901234123457")

    def test_more_labels_than_fit_spill_onto_another_page(self):
        one_page = service.generate_labels(
            {self.product_id: 24}, path=self.path("one.pdf"), sheet="a4-24"
        )
        two_pages = service.generate_labels(
            {self.product_id: 25}, path=self.path("two.pdf"), sheet="a4-24"
        )
        self.assertGreater(two_pages.stat().st_size, one_page.stat().st_size)

    def test_quantities_of_zero_are_skipped(self):
        items = service.build_items({self.product_id: 2, self.no_barcode_id: 0})
        self.assertEqual([item["count"] for item in items], [2])

    def test_an_empty_selection_is_refused(self):
        with self.assertRaises(service.LabelError):
            service.generate_labels({}, path=self.path())

    def test_a_missing_product_is_ignored(self):
        with self.assertRaises(service.LabelError):
            service.build_items({999999: 5})

    def test_an_absurd_quantity_is_refused(self):
        with self.assertRaises(service.LabelError) as caught:
            service.build_items({self.product_id: service.MAX_LABELS + 1})
        self.assertIn("at a time", str(caught.exception))

    def test_an_unknown_format_is_refused(self):
        with self.assertRaises(service.LabelError):
            service.generate_labels({self.product_id: 1}, path=self.path(), sheet="nope")

    def test_a_long_name_does_not_overflow_the_label(self):
        long_id = products_service.create_product(
            sku="LONG-1", price_usd="1.00",
            name="Extremely long product name that will never fit on a small label",
        )
        service.generate_labels({long_id: 1}, path=self.path(), sheet="roll-50x30")

    def test_an_ordinary_barcode_fits_the_smallest_label(self):
        # A 13-digit EAN on a 50 mm roll label was the case that first broke.
        service.generate_labels(
            {self.product_id: 1}, path=self.path("roll.pdf"), sheet="roll-50x30"
        )

    def test_a_code_too_long_for_the_label_is_refused_not_smeared(self):
        wide_id = products_service.create_product(
            sku="W-1", name="Wide", price_usd="1.00",
            barcode="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
        )
        with self.assertRaises(service.LabelError) as caught:
            service.generate_labels(
                {wide_id: 1}, path=self.path("wide.pdf"), sheet="roll-50x30"
            )
        self.assertIn("larger label", str(caught.exception))

    def test_the_same_long_code_prints_fine_on_a_bigger_label(self):
        wide_id = products_service.create_product(
            sku="W-1", name="Wide", price_usd="1.00",
            barcode="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
        )
        service.generate_labels({wide_id: 1}, path=self.path("big.pdf"), sheet="a4-24")

    def test_prices_can_be_shown_in_both_currencies(self):
        with_lbp = service.generate_labels(
            {self.product_id: 1}, path=self.path("lbp.pdf"), show_lbp=True, show_store=True
        )
        self.assertTrue(with_lbp.exists())

    def test_labels_can_be_printed_without_a_price(self):
        path = service.generate_labels(
            {self.product_id: 1}, path=self.path("bare.pdf"), show_price=False
        )
        self.assertTrue(path.exists())

    def test_the_symbol_never_overflows_its_label(self):
        # The 12.7 mm quiet zone reportlab adds by default made this fail for
        # every code on the 50 mm roll, so it is checked for each format.
        for key, spec in service.SHEETS.items():
            inner = spec.label_width - 4 * mm
            for code in ("5901234123457", "ABC-001", "12345678"):
                symbol = service._barcode(code, inner, 10 * mm)
                self.assertLessEqual(
                    symbol.width, inner + 0.01,
                    f"{code} overflows a {key} label",
                )
                self.assertGreaterEqual(symbol.barWidth, service.MIN_BAR_WIDTH)

    def test_every_grid_fits_its_page(self):
        for key, spec in service.SHEETS.items():
            width = spec.margin_x * 2 + spec.columns * spec.label_width
            height = spec.margin_y * 2 + spec.rows * spec.label_height
            self.assertLessEqual(width, spec.page_size[0] + 0.5, key)
            self.assertLessEqual(height, spec.page_size[1] + 0.5, key)

    def test_the_formats_are_offered_as_choices(self):
        choices = service.sheet_choices()
        self.assertIn("a4-24", [key for key, _ in choices])
        self.assertTrue(all(title for _, title in choices))


if __name__ == "__main__":
    unittest.main()
