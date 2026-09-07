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

import os
import time
import unittest

from app import config, db
from app.services import customers as customers_service
from app.services import products as products_service
from app.services import sales as sales_service
from app.services import suppliers as suppliers_service
from tests.support import DatabaseTestCase, destroy_tk_root

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


class DisplayPromiseTests(unittest.TestCase):
    """The screens are only covered where Tk can open a window.

    Every UI class below skips itself when there is no display, which is right
    on a machine that has none -- and dangerous on a machine that is supposed
    to. CI installs xvfb precisely so these run; if that ever stopped working
    the whole set would quietly vanish and the run would still be green,
    reporting success for tests that never executed. Setting
    RE4_REQUIRE_DISPLAY turns that silence into a failure.
    """

    def test_a_promised_display_is_really_there(self):
        if not os.environ.get("RE4_REQUIRE_DISPLAY"):
            self.skipTest("no display was promised, so skipping is honest here")
        self.assertTrue(
            HAS_DISPLAY,
            "RE4_REQUIRE_DISPLAY is set, so Tk was expected to open a window and "
            "could not - every screen test would have skipped in silence.",
        )


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
        destroy_tk_root(self.root)

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
        destroy_tk_root(self.root)

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
class TillMoneyInputTests(DatabaseTestCase):
    """A figure the till cannot read must never be quietly treated as nothing."""

    def setUp(self) -> None:
        super().setUp()
        self.product_id = products_service.create_product(
            sku="TILL-1", name="Sellable", price_usd=10, cost_usd=4, stock_qty=5
        )
        self.root = ctk.CTk()
        self.root.withdraw()
        self.addCleanup(lambda: destroy_tk_root(self.root))

    def _till(self):
        from app.ui.shell import AppShell

        shell = AppShell(self.root, self.admin, lambda: None)
        shell.grid(row=0, column=0, sticky="nsew")
        shell.show("pos")
        self.root.update_idletasks()
        view = shell._views["pos"]
        view._add_product(products_service.get_product(self.product_id))
        self.root.update_idletasks()
        return view

    def _complete(self, view):
        """Take payment with the two things that would talk to a person stubbed.

        ``offer_receipt`` puts a modal question on screen and then opens a PDF;
        neither belongs in a test run, and a dialog waiting for a click would
        hang the suite.
        """
        import app.ui.pos_view as pos_view

        errors = []
        original_error, original_receipt = pos_view.show_error, pos_view.offer_receipt
        pos_view.show_error = lambda *args, **kwargs: errors.append(args)
        pos_view.offer_receipt = lambda *args, **kwargs: None
        try:
            view._complete_sale()
        finally:
            pos_view.show_error = original_error
            pos_view.offer_receipt = original_receipt
        self.root.update_idletasks()
        return errors

    def test_an_unreadable_discount_stops_the_sale_instead_of_being_dropped(self):
        """The bug: "1O" became a $0 discount and the customer paid full price.

        The running total is parsed on every keystroke and must stay quiet
        about half-typed input, so the check that matters is the one taken
        when the money changes hands.
        """
        view = self._till()
        view.discount_var.set("1O")       # a letter O, as typed by a person
        view.paid_var.set("100")

        errors = self._complete(view)

        self.assertEqual(len(errors), 1, "the cashier should have been told")
        self.assertEqual(
            db.scalar("SELECT COUNT(*) FROM sales", default=0), 0,
            "no sale may be recorded when the discount could not be read",
        )

    def test_a_blank_discount_is_still_simply_nothing(self):
        view = self._till()
        view.discount_var.set("")
        view.paid_var.set("100")

        self.assertEqual(self._complete(view), [])
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM sales", default=0), 1)

    def test_a_readable_discount_is_taken_off(self):
        view = self._till()
        view.discount_var.set("2.50")
        view.paid_var.set("100")

        self.assertEqual(self._complete(view), [])
        self.assertAlmostEqual(
            db.scalar("SELECT discount_usd FROM sales", default=0), 2.50, places=2
        )


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
        destroy_tk_root(self.root)

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
        self.addCleanup(lambda: destroy_tk_root(self.root))

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
        self.addCleanup(lambda: destroy_tk_root(self.root))

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
    """A search box must query once, after the typing stops — not per letter."""

    opens_shift = False

    def setUp(self) -> None:
        super().setUp()
        self.root = ctk.CTk()
        self.root.withdraw()
        self.addCleanup(self._destroy_root)

    def _destroy_root(self) -> None:
        destroy_tk_root(self.root)

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


def _ui_modules():
    """Every app.ui module, imported, so their classes can be inspected."""
    import importlib
    import pkgutil

    import app.ui

    modules = []
    for info in pkgutil.iter_modules(app.ui.__path__):
        modules.append(importlib.import_module(f"app.ui.{info.name}"))
    return modules


@unittest.skipUnless(HAS_DISPLAY, "no display available for Tk")
class ModalTeardownTests(DatabaseTestCase):
    """A modal that is closed at once must not leave a timer pointing at it."""

    opens_shift = False

    def setUp(self) -> None:
        super().setUp()
        self.root = ctk.CTk()
        self.root.withdraw()
        self.addCleanup(lambda: destroy_tk_root(self.root))

    def test_no_dialog_writes_its_own_grab(self):
        """Six of them once did, and fixing one reached none of the others.

        A dialog that hand-rolls the grab gets the deferred callback without
        the cancellation, which is the bug this mixin exists to hold shut. A
        window that genuinely should not take the keyboard is free to say
        nothing at all; what it may not do is write its own.
        """
        from app.ui import widgets

        offenders = []
        for module in _ui_modules():
            for name, obj in vars(module).items():
                if not isinstance(obj, type) or not issubclass(obj, ctk.CTkToplevel):
                    continue
                if obj.__module__ != module.__name__:
                    continue  # imported, not declared here
                if "_grab" in vars(obj) or "claim_keyboard" in vars(obj):
                    offenders.append(f"{module.__name__}.{name}")

        self.assertEqual(
            offenders,
            [],
            "these define their own keyboard grab instead of mixing in "
            f"widgets.GrabsKeyboard: {', '.join(offenders)}",
        )
        # And the mixin really is reaching them, rather than everyone having
        # quietly stopped grabbing at all.
        users = [
            f"{module.__name__}.{name}"
            for module in _ui_modules()
            for name, obj in vars(module).items()
            if isinstance(obj, type)
            and issubclass(obj, ctk.CTkToplevel)
            and obj.__module__ == module.__name__
            and issubclass(obj, widgets.GrabsKeyboard)
        ]
        self.assertGreaterEqual(len(users), 5, f"only {users} mix it in")

    def test_the_mixin_cancels_the_grab_for_any_window(self):
        """Proved on the mixin itself, not only through Modal."""
        from app.ui import widgets

        class BareDialog(widgets.GrabsKeyboard, ctk.CTkToplevel):
            pass

        dialog = BareDialog(self.root)
        job = dialog.defer(80, dialog._grab)
        self.assertIn(job, dialog._deferred)
        self.assertIn(job, self.root.tk.call("after", "info"))

        dialog.destroy()
        self.assertNotIn(job, self.root.tk.call("after", "info"))

    def test_a_window_that_never_grabbed_still_destroys(self):
        """The job is held on the class, so an untouched window reads None."""
        from app.ui import widgets

        class BareDialog(widgets.GrabsKeyboard, ctk.CTkToplevel):
            pass

        BareDialog(self.root).destroy()  # must not raise

    def test_a_form_closed_at_once_leaves_no_focus_timer(self):
        """FormModal defers putting the cursor in the first box by 120ms.

        "_focus_first" was one of the names in the burst of Tcl errors that
        started all this, so this is the case that was actually happening
        rather than one that could.
        """
        from app.ui import widgets

        form = widgets.FormModal(
            self.root, "Quick", [{"key": "name", "label": "Name"}]
        )
        pending = set(form._deferred or ())
        self.assertGreaterEqual(
            len(pending), 2, "expected both the grab and the focus to be pending"
        )
        form.destroy()
        self.assertEqual(pending & set(self.root.tk.call("after", "info")), set())

    def test_a_screen_swapped_out_at_once_leaves_no_focus_timer(self):
        """The sign-in frame goes the moment the password is accepted.

        On a remembered password that is comfortably inside the 150ms it waits
        before putting the cursor in the username box.
        """
        from app.ui.login import LoginView

        view = LoginView(self.root, lambda _user: None)
        pending = set(view._deferred or ())
        self.assertTrue(pending, "nothing was deferred, so this proves nothing")
        view.destroy()
        self.assertEqual(pending & set(self.root.tk.call("after", "info")), set())

    def test_dismissing_a_modal_cancels_its_pending_grab(self):
        """Otherwise Tcl reaches a callback whose command destroy() deleted.

        It reports that as "invalid command name ..." on stderr, which is an
        error message for something that is not an error -- and a shop looking
        at a log full of those has no way to spot the one that matters.
        """
        from app.ui import widgets

        modal = widgets.Modal(self.root, "Closed straight away")
        pending = set(modal._deferred or ())
        self.assertTrue(pending, "nothing was deferred, so this proves nothing")
        self.assertTrue(pending <= set(self.root.tk.call("after", "info")))

        modal.destroy()
        still_queued = pending & set(self.root.tk.call("after", "info"))
        self.assertEqual(still_queued, set())
