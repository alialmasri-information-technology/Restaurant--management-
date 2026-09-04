"""What needs somebody, right now.

The thing worth testing here is restraint. A briefing that fires on a quiet shop
teaches people to ignore it, so most of these assert that nothing is said — and
the rest assert that the sentence said is one a person would actually say.
"""

from __future__ import annotations

import datetime as dt

from app import db
from app.services import accounts as accounts_service
from app.services import briefing
from app.services import customers as customers_service
from app.services import products as products_service
from app.services import purchases as purchases_service
from app.services import sales as sales_service
from app.services import settings as settings_service
from app.services import shifts as shifts_service
from app.services import stocktake as stocktake_service
from app.services import suppliers as suppliers_service
from tests.support import DatabaseTestCase


class BriefingTestCase(DatabaseTestCase):
    def setUp(self) -> None:
        super().setUp()
        # A backup so housekeeping stays quiet unless a test asks about it.
        from app.services import backups as backups_service

        backups_service.create("test")
        self.product = products_service.create_product(
            sku="B-1", name="Briefed Widget", price_usd=10, cost_usd=4,
            stock_qty=50, reorder_level=5,
        )

    def texts(self, **kwargs) -> list[str]:
        return [note.text for note in briefing.notes(**kwargs)]

    def screens(self, **kwargs) -> list[str]:
        return [note.screen for note in briefing.notes(**kwargs)]


class QuietShop(BriefingTestCase):
    def test_a_shop_with_nothing_outstanding_says_nothing(self):
        self.assertEqual(briefing.notes(), [])

    def test_all_clear_agrees(self):
        self.assertTrue(briefing.all_clear())


class Till(BriefingTestCase):
    opens_shift = False

    def test_a_closed_till_is_the_first_thing_said(self):
        notes = briefing.notes()
        self.assertTrue(notes)
        self.assertIn("till is closed", notes[0].text)
        self.assertEqual(notes[0].screen, "till")
        self.assertEqual(notes[0].tone, briefing.WARN)

    def test_a_shop_that_does_not_insist_on_a_till_is_not_nagged(self):
        settings_service.set_value("require_shift", "0")
        self.assertEqual(briefing.notes(), [])

    def test_an_open_till_from_today_is_not_worth_mentioning(self):
        shifts_service.open_shift(self.admin.user_id, opening_float=100)
        self.assertEqual(briefing.notes(), [])

    def test_a_till_left_open_overnight_is(self):
        shift_id = shifts_service.open_shift(self.admin.user_id, opening_float=100)
        yesterday = (dt.date.today() - dt.timedelta(days=1)).isoformat()
        db.execute(
            "UPDATE shifts SET opened_at = ? WHERE shift_id = ?",
            (f"{yesterday} 09:00:00", shift_id),
        )
        notes = briefing.notes()
        self.assertEqual(len(notes), 1)
        self.assertIn("has been open since", notes[0].text)
        self.assertEqual(notes[0].tone, briefing.WARN)


class Stock(BriefingTestCase):
    def test_a_full_shelf_says_nothing(self):
        self.assertEqual(briefing.notes(), [])

    def test_running_low_is_a_note_not_a_warning(self):
        products_service.adjust_stock(self.product, -46, reason="Sale")
        notes = briefing.notes()
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].text, "1 product below the reorder level.")
        self.assertEqual(notes[0].tone, briefing.NOTE)

    def test_being_out_of_stock_is_a_warning(self):
        products_service.adjust_stock(self.product, -50, reason="Sale")
        notes = briefing.notes()
        self.assertEqual(notes[0].text, "1 product completely out of stock.")
        self.assertEqual(notes[0].tone, briefing.WARN)

    def test_out_and_low_are_said_in_one_sentence(self):
        second = products_service.create_product(
            sku="B-2", name="Also Low", price_usd=3, stock_qty=2, reorder_level=5
        )
        self.assertTrue(second)
        products_service.adjust_stock(self.product, -50, reason="Sale")
        notes = briefing.notes()
        self.assertEqual(
            notes[0].text, "1 product completely out of stock, 1 more running low."
        )


class Accounts(BriefingTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.customer = customers_service.create_customer(name="Cafe Nadia")
        accounts_service.set_credit_limit(self.customer, 100)

    def sell_on_credit(self, qty: int) -> int:
        cart = sales_service.Cart()
        cart.add_product(products_service.get_product(self.product), qty)
        return sales_service.create_sale(
            user_id=self.admin.user_id, cart=cart,
            customer_id=self.customer, payment_method="Credit",
        )

    def test_a_customer_who_owes_is_mentioned_with_the_total(self):
        self.sell_on_credit(3)
        texts = self.texts()
        self.assertIn("1 customer owes you $30.00.", texts)

    def test_being_at_the_limit_is_a_warning_of_its_own(self):
        self.sell_on_credit(10)  # exactly the 100.00 limit
        notes = briefing.notes()
        limit_note = next(n for n in notes if "credit limit" in n.text)
        self.assertEqual(
            limit_note.text,
            "Cafe Nadia is at their credit limit and cannot buy on account.",
        )
        self.assertEqual(limit_note.tone, briefing.WARN)

    def test_comfortably_inside_the_limit_raises_no_warning(self):
        self.sell_on_credit(2)
        self.assertFalse(any("credit limit" in text for text in self.texts()))

    def test_a_settled_account_is_not_mentioned_at_all(self):
        self.sell_on_credit(2)
        accounts_service.record_payment(self.customer, 20, user_id=self.admin.user_id)
        self.assertEqual(briefing.notes(), [])

    def test_several_at_the_limit_are_listed_and_the_verb_agrees(self):
        second = customers_service.create_customer(name="Corner Shop")
        accounts_service.set_credit_limit(second, 10)
        self.sell_on_credit(10)
        cart = sales_service.Cart()
        cart.add_product(products_service.get_product(self.product), 1)
        sales_service.create_sale(
            user_id=self.admin.user_id, cart=cart,
            customer_id=second, payment_method="Credit",
        )
        limit_note = next(n for n in briefing.notes() if "credit limit" in n.text)
        self.assertIn("are at their credit limit", limit_note.text)
        self.assertIn("Cafe Nadia", limit_note.text)
        self.assertIn("Corner Shop", limit_note.text)


class OtherWork(BriefingTestCase):
    def test_an_open_stock_take_reports_its_progress(self):
        take_id = stocktake_service.open_count(self.admin.user_id)
        self.assertTrue(take_id)
        note = next(n for n in briefing.notes() if "Stock take" in n.text)
        self.assertIn("0 of 1 lines counted", note.text)
        self.assertEqual(note.screen, "stocktake")

    def test_an_applied_stock_take_is_not_still_open(self):
        take_id = stocktake_service.open_count(self.admin.user_id)
        stocktake_service.record_count(take_id, self.product, 50)
        stocktake_service.apply_count(take_id, self.admin.user_id)
        self.assertFalse(any("Stock take" in text for text in self.texts()))

    def test_a_parked_sale_is_waiting_for_somebody(self):
        cart = sales_service.Cart()
        cart.add_product(products_service.get_product(self.product), 1)
        sales_service.park_sale(cart, self.admin.user_id, label="Held")
        self.assertIn(
            "1 sale still parked, waiting to be finished.", self.texts()
        )

    def test_an_ordered_purchase_order_is_still_waiting(self):
        supplier = suppliers_service.create_supplier(name="Acme")
        po_id = purchases_service.create_po(
            supplier_id=supplier, lines=[(self.product, 5, 4)], user_id=self.admin.user_id
        )
        purchases_service.set_status(po_id, "Ordered")
        self.assertIn(
            "1 purchase order still waiting to be received.", self.texts()
        )

    def test_a_draft_order_is_not_waiting_on_anybody(self):
        supplier = suppliers_service.create_supplier(name="Acme")
        purchases_service.create_po(
            supplier_id=supplier, lines=[(self.product, 5, 4)], user_id=self.admin.user_id
        )
        self.assertFalse(any("purchase order" in text for text in self.texts()))


class Housekeeping(BriefingTestCase):
    def test_a_stale_backup_is_mentioned(self):
        stale = dt.datetime.now() - dt.timedelta(days=briefing.BACKUP_STALE_DAYS + 1)
        from app.services import backups as backups_service

        for entry in backups_service.list_backups():
            import os

            os.utime(entry["path"], (stale.timestamp(), stale.timestamp()))
        texts = self.texts()
        self.assertTrue(any("last backup was" in text for text in texts), texts)

    def test_a_fresh_backup_is_not(self):
        self.assertFalse(any("backup" in text for text in self.texts()))

    def test_a_locked_account_is_named(self):
        from app import auth

        auth.create_user("butterfingers", "a-good-password", "Employee")
        for _ in range(10):
            try:
                auth.authenticate("butterfingers", "wrong")
            except auth.AuthError:
                pass
        texts = self.texts()
        self.assertTrue(any("butterfingers" in text for text in texts), texts)


class WhoSeesWhat(BriefingTestCase):
    def test_staff_are_not_shown_work_they_cannot_do(self):
        supplier = suppliers_service.create_supplier(name="Acme")
        po_id = purchases_service.create_po(
            supplier_id=supplier, lines=[(self.product, 5, 4)], user_id=self.admin.user_id
        )
        purchases_service.set_status(po_id, "Ordered")

        self.assertTrue(any("purchase order" in t for t in self.texts(is_admin=True)))
        self.assertFalse(any("purchase order" in t for t in self.texts(is_admin=False)))

    def test_staff_still_see_the_things_they_can_act_on(self):
        products_service.adjust_stock(self.product, -50, reason="Sale")
        self.assertTrue(any("out of stock" in t for t in self.texts(is_admin=False)))


class Ordering(BriefingTestCase):
    opens_shift = False

    def test_warnings_come_before_notes(self):
        cart = sales_service.Cart()
        cart.add_product(products_service.get_product(self.product), 1)
        sales_service.park_sale(cart, self.admin.user_id)

        tones = [note.tone for note in briefing.notes()]
        self.assertEqual(tones, sorted(tones, key=lambda t: briefing._ORDER[t]))
        self.assertEqual(tones[0], briefing.WARN)

    def test_every_note_points_at_a_screen(self):
        products_service.adjust_stock(self.product, -50, reason="Sale")
        for note in briefing.notes():
            self.assertTrue(note.screen, note.text)
