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
