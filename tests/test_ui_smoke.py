"""Builds every screen against a real (hidden) Tk root.

The service layer is covered thoroughly and the widgets are not, which leaves
the most common way to break this application entirely untested: a view that
raises on construction or on its first refresh. Nothing here asserts what a
screen looks like — only that it can be built, refreshed and navigated to
without throwing, which is exactly the failure a released build would show as a
blank window.

Skipped automatically where there is no display (headless CI), so it costs
nothing there and catches the regression on a developer's machine.
"""

from __future__ import annotations

import time
import unittest

from app import config
from app.services import customers as customers_service
from app.services import products as products_service
from app.services import sales as sales_service
from app.services import suppliers as suppliers_service
from tests.support import DatabaseTestCase

try:
    import tkinter as tk

    import customtkinter as ctk
except ImportError:  # pragma: no cover - Tk missing entirely
    tk = None
    ctk = None


def _display_available() -> bool:
    if tk is None:
        return False
    try:
        root = tk.Tk()
    except Exception:  # noqa: BLE001 - any Tcl/display failure means "skip"
        return False
    root.destroy()
    return True


HAS_DISPLAY = _display_available()


def _tear_down(root) -> None:
    """Destroy a Tk root without the usual burst of Tcl background errors.

    CustomTkinter schedules deferred work with ``after`` — icon fixes, focus
    grabs. Destroying the root while any of it is still queued makes Tcl shout
    about an invalid command for every one. Cancelling first keeps the test
    output readable.
    """
    try:
        for job in root.tk.call("after", "info"):
            try:
                root.after_cancel(job)
            except Exception:  # noqa: BLE001 - already fired
                pass
        root.update_idletasks()
        root.destroy()
    except Exception:  # noqa: BLE001 - already gone
        pass


@unittest.skipUnless(HAS_DISPLAY, "no display available for Tk")
class ViewSmokeTests(DatabaseTestCase):
    """Every navigable screen builds and refreshes with data behind it."""

    def setUp(self) -> None:
        super().setUp()
        self._seed()
        self.root = ctk.CTk()
        self.root.withdraw()  # never flash a window across the screen
        self.addCleanup(self._destroy_root)

    def _destroy_root(self) -> None:
        _tear_down(self.root)

    def _seed(self) -> None:
        supplier_id = suppliers_service.create_supplier(name="Test Supplier")
        category_id = products_service.create_category("Tested")
        product_id = products_service.create_product(
            sku="SMOKE-1", name="Smoke Widget", price_usd=5, cost_usd=2,
            stock_qty=10, barcode="9001", category_id=category_id,
            supplier_id=supplier_id,
        )
        customers_service.create_customer(name="Smoke Customer", phone="123")

        cart = sales_service.Cart()
        cart.add_product(products_service.get_product(product_id), 2)
        sales_service.create_sale(
            user_id=self.admin.user_id, cart=cart, amount_paid=50
        )

    def _shell(self):
        from app.ui.shell import AppShell

        shell = AppShell(self.root, self.admin, lambda: None)
        shell.grid(row=0, column=0, sticky="nsew")
        self.root.update_idletasks()
        return shell

    def test_the_shell_builds_and_lands_on_the_dashboard(self):
        shell = self._shell()
        self.assertEqual(shell.current_key, "dashboard")

    def test_every_screen_builds_and_refreshes(self):
        from app.ui.shell import NAV_ITEMS

        shell = self._shell()
        for key, label, _admin_only in NAV_ITEMS:
            with self.subTest(screen=label):
                shell.show(key)
                self.root.update_idletasks()
                self.assertEqual(shell.current_key, key)

    def test_an_employee_never_sees_an_admin_screen(self):
        from app import auth
        from app.ui.shell import AppShell

        auth.create_user("smoke-employee", "employee-pass", config.ROLE_EMPLOYEE)
        employee = auth.authenticate("smoke-employee", "employee-pass")
        shell = AppShell(self.root, employee, lambda: None)
        shell.grid(row=0, column=0, sticky="nsew")
        self.root.update_idletasks()

        self.assertNotIn("settings", shell.nav_buttons)
        self.assertNotIn("reports", shell.nav_buttons)
        # Asking for one directly is refused too, not merely hidden from the menu.
        shell.show("settings")
        self.assertEqual(shell.current_key, "dashboard")

    def test_staff_can_reach_the_stock_take_screen(self):
        shell = self._shell()
        self.assertIn("stocktake", shell.nav_buttons)
        shell.show("stocktake")
        self.root.update_idletasks()
        self.assertEqual(shell.current_key, "stocktake")

    def test_the_login_screen_builds(self):
        from app.ui.login import LoginView

        view = LoginView(self.root, lambda _user: None)
        view.grid(row=0, column=0, sticky="nsew")
        self.root.update_idletasks()
        self.assertTrue(view.winfo_exists())

    def test_the_lock_screen_builds_and_checks_the_password(self):
        from app.ui.security import LockScreen

        unlocked = []
        view = LockScreen(
            self.root, self.admin, lambda: unlocked.append(True), lambda: None
        )
        view.grid(row=0, column=0, sticky="nsew")
        self.root.update_idletasks()

        view.password.set("not-the-password")
        view.attempt()
        self.assertEqual(unlocked, [])

        view.password.set("admin123")
        view.attempt()
        self.assertEqual(unlocked, [True])


@unittest.skipUnless(HAS_DISPLAY, "no display available for Tk")
class StockTakeScreenTests(DatabaseTestCase):
    """The stock take screen against a count that is actually open."""

    def setUp(self) -> None:
        super().setUp()
        from app.services import stocktake as stocktake_service

        self.product_id = products_service.create_product(
            sku="CNT-1", name="Counted Thing", price_usd=3, cost_usd=1, stock_qty=12
        )
        self.take_id = stocktake_service.open_count(self.admin.user_id)

        self.root = ctk.CTk()
        self.root.withdraw()
        self.addCleanup(self._destroy_root)

    def _destroy_root(self) -> None:
        _tear_down(self.root)

    def test_the_sheet_renders_and_scanning_updates_it(self):
        from app.ui.shell import AppShell

        shell = AppShell(self.root, self.admin, lambda: None)
        shell.grid(row=0, column=0, sticky="nsew")
        shell.show("stocktake")
        self.root.update_idletasks()

        view = shell._views["stocktake"]
        self.assertIsNotNone(view.count)

        view.scan_var.set("CNT-1")
        view._scan()
        self.root.update_idletasks()

        from app.services import stocktake as stocktake_service

        line = stocktake_service.list_items(self.take_id)[0]
        self.assertEqual(line["counted_qty"], 1)


@unittest.skipUnless(HAS_DISPLAY, "no display available for Tk")
class CustomerAccountScreenTests(DatabaseTestCase):
    """The customers screen with a real debt behind it."""

    def setUp(self) -> None:
        super().setUp()
        from app.services import accounts
        from app.services import customers as customers_service

        self.product_id = products_service.create_product(
            sku="ACC-1", name="On Account", price_usd=20, cost_usd=8, stock_qty=50
        )
        self.customer_id = customers_service.create_customer(name="Owing Customer")
        accounts.set_credit_limit(self.customer_id, 200)

        cart = sales_service.Cart()
        cart.add_product(products_service.get_product(self.product_id), 3)
        sales_service.create_sale(
            user_id=self.admin.user_id, cart=cart,
            customer_id=self.customer_id, payment_method="Credit",
        )

        self.root = ctk.CTk()
        self.root.withdraw()
        self.addCleanup(self._destroy_root)

    def _destroy_root(self) -> None:
        _tear_down(self.root)

    def _shell(self):
        from app.ui.shell import AppShell

        shell = AppShell(self.root, self.admin, lambda: None)
        shell.grid(row=0, column=0, sticky="nsew")
        self.root.update_idletasks()
        return shell

    def test_the_customer_list_shows_the_balance(self):
        shell = self._shell()
        shell.show("customers")
        self.root.update_idletasks()

        view = shell._views["customers"]
        row = customers_service.list_customers()[0]
        self.assertAlmostEqual(row["balance_usd"], 60.0, places=2)
        self.assertIn("60", view.receivable_label.cget("text"))

    def test_the_owing_filter_narrows_the_list(self):
        from app.services import customers as customers_service

        customers_service.create_customer(name="Owes Nothing")
        shell = self._shell()
        shell.show("customers")
        self.root.update_idletasks()

        view = shell._views["customers"]
        view.owing_only.select()
        view.refresh()
        self.root.update_idletasks()
        self.assertEqual(len(view.table.tree.get_children()), 1)

    def test_the_statement_builds_with_a_running_balance(self):
        from app.ui.customers_view import StatementModal

        shell = self._shell()
        shell.show("customers")
        self.root.update_idletasks()

        customer = customers_service.get_customer(self.customer_id)
        modal = StatementModal(self.root, customer, shell)
        self.root.update_idletasks()
        self.assertEqual(len(modal.table.tree.get_children()), 1)
        self.assertIn("60", modal.heading.cget("text"))
        modal.on_cancel()

    def test_the_reports_screen_lists_the_debtor(self):
        shell = self._shell()
        shell.show("reports")
        self.root.update_idletasks()

        view = shell._views["reports"]
        self.assertEqual(len(view.owed.tree.get_children()), 1)


@unittest.skipUnless(HAS_DISPLAY, "no display available for Tk")
class DayReportSmokeTests(DatabaseTestCase):
    """The end-of-day sheet, reached the way a shopkeeper reaches it."""

    def setUp(self) -> None:
        super().setUp()
        product_id = products_service.create_product(
            sku="DAY-1", name="Day Widget", price_usd=5, cost_usd=2, stock_qty=10
        )
        cart = sales_service.Cart()
        cart.add_product(products_service.get_product(product_id), 2)
        sales_service.create_sale(user_id=self.admin.user_id, cart=cart, amount_paid=10)

        self.root = ctk.CTk()
        self.root.withdraw()
        self.addCleanup(lambda: _tear_down(self.root))

    def _shell(self):
        from app.ui.shell import AppShell

        shell = AppShell(self.root, self.admin, lambda: None)
        shell.grid(row=0, column=0, sticky="nsew")
        self.root.update_idletasks()
        return shell

    def test_the_till_screen_can_produce_one(self):
        from app.ui import receipt_actions

        shell = self._shell()
        shell.show("till")
        self.root.update_idletasks()

        produced = []
        original = receipt_actions.open_file
        receipt_actions.open_file = produced.append
        try:
            shell._views["till"].print_day_report()
        finally:
            receipt_actions.open_file = original

        self.assertEqual(len(produced), 1)
        self.assertTrue(produced[0].exists())

    def test_the_reports_screen_can_reprint_one(self):
        from app.ui import receipt_actions

        shell = self._shell()
        shell.show("reports")
        self.root.update_idletasks()

        produced = []
        original = receipt_actions.open_file
        receipt_actions.open_file = produced.append
        try:
            shell._views["reports"]._day_report()
        finally:
            receipt_actions.open_file = original

        self.assertEqual(len(produced), 1)
        self.assertTrue(produced[0].exists())


@unittest.skipUnless(HAS_DISPLAY, "no display available for Tk")
class BriefingSmokeTests(DatabaseTestCase):
    """The dashboard's "what needs you" panel, and the greeting above it."""

    opens_shift = False

    def setUp(self) -> None:
        super().setUp()
        from app.services import backups as backups_service

        backups_service.create("test")
        self.root = ctk.CTk()
        self.root.withdraw()
        self.addCleanup(lambda: _tear_down(self.root))

    def _dashboard(self):
        from app.ui.shell import AppShell

        shell = AppShell(self.root, self.admin, lambda: None)
        shell.grid(row=0, column=0, sticky="nsew")
        self.root.update_idletasks()
        return shell, shell._views["dashboard"]

    def test_the_header_greets_the_person_signed_in(self):
        _shell, view = self._dashboard()
        self.assertIn(
            self.admin.display_name.split(" ")[0], view.header.title_label.cget("text")
        )

    def test_an_outstanding_note_is_rendered(self):
        # No till is open, which the briefing should say out loud.
        _shell, view = self._dashboard()
        self.assertTrue(view.briefing._rows)

    def test_a_note_navigates_to_the_screen_that_fixes_it(self):
        shell, view = self._dashboard()
        row = view.briefing._rows[0]
        button = next(
            child for child in row.winfo_children() if isinstance(child, ctk.CTkButton)
        )
        button.invoke()
        self.root.update_idletasks()
        self.assertEqual(shell.current_key, "till")

    def test_a_quiet_shop_gets_the_all_clear_line(self):
        from app.services import shifts as shifts_service

        shifts_service.open_shift(self.admin.user_id, opening_float=100)
        _shell, view = self._dashboard()
        view.refresh()
        self.root.update_idletasks()
        self.assertEqual(len(view.briefing._rows), 1)
        labels = [
            child.cget("text") for child in view.briefing._rows[0].winfo_children()
            if isinstance(child, ctk.CTkLabel)
        ]
        self.assertTrue(any("good shape" in text for text in labels), labels)

    def test_staff_are_not_offered_a_screen_they_cannot_open(self):
        """A briefing line must never promise somewhere an employee cannot go."""
        from app import auth
        from app.ui.shell import AppShell

        auth.create_user("briefed", "employee-pass", config.ROLE_EMPLOYEE)
        employee = auth.authenticate("briefed", "employee-pass")
        shell = AppShell(self.root, employee, lambda: None)
        shell.grid(row=0, column=0, sticky="nsew")
        self.root.update_idletasks()

        for row in shell._views["dashboard"].briefing._rows:
            for child in row.winfo_children():
                if isinstance(child, ctk.CTkButton):
                    child.invoke()
                    self.root.update_idletasks()
                    self.assertIn(shell.current_key, shell.nav_buttons)


class DebounceTests(DatabaseTestCase):
    """A search box must query once, after the typing stops � not per letter."""

    opens_shift = False

    def setUp(self) -> None:
        super().setUp()
        self.root = ctk.CTk()
        self.root.withdraw()
        self.addCleanup(self._destroy_root)

    def _destroy_root(self) -> None:
        _tear_down(self.root)

    def _settle(self, condition) -> bool:
        """Pump the event loop until the deferred call runs, or fail trying."""
        deadline = time.monotonic() + 5
        while not condition() and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.005)
        return condition()

    def test_three_keystrokes_run_the_query_once(self):
        from app.ui import widgets

        calls: list[int] = []
        keystroke = widgets.debounce(self.root, 40, lambda: calls.append(1))
        keystroke()
        keystroke()
        keystroke()
        self.assertEqual(calls, [])
        self.assertTrue(self._settle(lambda: bool(calls)))
        self.assertEqual(calls, [1])

    def test_typing_after_a_pause_runs_the_query_again(self):
        from app.ui import widgets

        calls: list[int] = []
        keystroke = widgets.debounce(self.root, 40, lambda: calls.append(1))
        keystroke()
        self.assertTrue(self._settle(lambda: bool(calls)))
        self.assertEqual(len(calls), 1)

        keystroke()
        self.assertTrue(self._settle(lambda: len(calls) > 1))
        self.assertEqual(len(calls), 2)
