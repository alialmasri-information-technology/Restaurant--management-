"""Suppliers, and what happens when you try to delete one that is in use."""

from __future__ import annotations

import unittest

from app import config
from app.services import products as products_service
from app.services import purchases as purchases_service
from app.services import suppliers as service
from tests.support import DatabaseTestCase


class SupplierTests(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        self.supplier_id = service.create_supplier(
            name="Levant Wholesale", email="orders@levant.example", phone="01 555 123"
        )

    def test_a_supplier_is_stored_and_found_by_name(self):
        supplier = service.get_supplier(self.supplier_id)
        self.assertEqual(supplier["name"], "Levant Wholesale")
        self.assertEqual(
            service.find_by_name("levant wholesale")["supplier_id"], self.supplier_id
        )

    def test_a_blank_name_is_refused(self):
        with self.assertRaises(service.SupplierError):
            service.create_supplier(name="   ")

    def test_a_duplicate_name_is_refused(self):
        with self.assertRaises(service.SupplierError):
            service.create_supplier(name="Levant Wholesale")

    def test_a_malformed_email_is_refused(self):
        with self.assertRaises(service.SupplierError):
            service.create_supplier(name="Other", email="not-an-email")

    def test_renaming_onto_another_supplier_is_refused(self):
        other_id = service.create_supplier(name="Beirut Supply")
        with self.assertRaises(service.SupplierError):
            service.update_supplier(other_id, name="Levant Wholesale")

    def test_an_unused_supplier_is_deleted_outright(self):
        service.delete_supplier(self.supplier_id)
        self.assertIsNone(service.get_supplier(self.supplier_id))

    def test_a_supplier_with_products_is_archived_not_deleted(self):
        products_service.create_product(
            sku="S1", name="Widget", price_usd="10.00", supplier_id=self.supplier_id
        )
        with self.assertRaises(service.SupplierError):
            service.delete_supplier(self.supplier_id)

        supplier = service.get_supplier(self.supplier_id)
        self.assertIsNotNone(supplier)
        self.assertEqual(supplier["is_active"], 0)

    def test_a_supplier_with_orders_is_archived_not_deleted(self):
        product_id = products_service.create_product(
            sku="S1", name="Widget", price_usd="10.00"
        )
        purchases_service.create_po(
            supplier_id=self.supplier_id, user_id=self.admin.user_id,
            lines=[(product_id, 1, "5.00")], status=config.PO_ORDERED,
        )
        with self.assertRaises(service.SupplierError):
            service.delete_supplier(self.supplier_id)
        self.assertEqual(service.get_supplier(self.supplier_id)["is_active"], 0)

    def test_archived_suppliers_are_hidden_by_default(self):
        service.update_supplier(self.supplier_id, name="Levant Wholesale", is_active=False)
        self.assertEqual(service.list_suppliers(), [])
        self.assertEqual(len(service.list_suppliers(include_inactive=True)), 1)

    def test_suppliers_can_be_searched(self):
        service.create_supplier(name="Beirut Supply")
        self.assertEqual(len(service.list_suppliers(search="beirut")), 1)
        self.assertEqual(len(service.list_suppliers(search="nobody")), 0)

    def test_the_product_count_is_reported(self):
        products_service.create_product(
            sku="S1", name="Widget", price_usd="10.00", supplier_id=self.supplier_id
        )
        self.assertEqual(service.get_supplier(self.supplier_id)["product_count"], 1)
        self.assertEqual(len(service.products_for(self.supplier_id)), 1)


if __name__ == "__main__":
    unittest.main()
