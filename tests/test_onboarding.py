"""The first-run walkthrough: asked once, and only by an administrator."""

from __future__ import annotations

import tkinter as tk
import unittest

import customtkinter as ctk

from app import auth, config
from app.services import settings as settings_service
from tests.support import DatabaseTestCase, destroy_tk_root

try:
    from app.ui import onboarding
except ImportError:  # pragma: no cover - Tk missing entirely
    onboarding = None

HAS_DISPLAY = None


def _display_available():
    global HAS_DISPLAY
    if HAS_DISPLAY is None:
        try:
            root = tk.Tk()
        except Exception:  # noqa: BLE001 - any Tcl/display failure means "skip"
            HAS_DISPLAY = False
        else:
            root.destroy()
            HAS_DISPLAY = True
    return HAS_DISPLAY


@unittest.skipUnless(_display_available() and onboarding, "no display available for Tk")
class FirstRunTests(DatabaseTestCase):
    opens_shift = False

    def setUp(self) -> None:
        super().setUp()
        self.root = ctk.CTk()
        self.root.withdraw()
        self.addCleanup(lambda: destroy_tk_root(self.root))

    def _admin(self):
        return auth.authenticate("admin", "admin123")

    def test_an_administrator_with_an_unopened_shop_gets_the_walkthrough(self):
        settings_service.set_value("first_run_done", "0")
        self.assertTrue(onboarding.maybe_first_run(self.root, self._admin()))
        self.assertTrue(any(
            isinstance(widget, (tk.Toplevel, ctk.CTkToplevel))
            for widget in self.root.winfo_children()
        ))

    def test_once_answered_it_never_asks_again(self):
        settings_service.set_value("first_run_done", "1")
        self.assertFalse(onboarding.maybe_first_run(self.root, self._admin()))

    def test_an_employee_is_never_asked(self):
        auth.create_user("staffer", "employee-pass", config.ROLE_EMPLOYEE)
        employee = auth.authenticate("staffer", "employee-pass")
        self.assertFalse(onboarding.needed(employee))

    def test_the_answers_reach_the_settings(self):
        settings_service.set_value("first_run_done", "0")
        wizard = onboarding.FirstRunWizard(self.root)
        wizard._save({
            "store_name": "Khoury's Corner",
            "store_phone": "01 555 030",
            "store_address": "Beirut",
            "exchange_rate": "89500",
            "tax_rate": "0",
            "require_shift": True,
        })
        values = settings_service.get_all()
        self.assertEqual(values["store_name"], "Khoury's Corner")
        self.assertEqual(values["exchange_rate"], "89500")
        self.assertEqual(values["first_run_done"], "1")

    def test_a_rubbish_exchange_rate_is_refused_and_not_saved(self):
        settings_service.set_value("first_run_done", "0")
        wizard = onboarding.FirstRunWizard(self.root)
        with self.assertRaises(ValueError):
            wizard._save({
                "store_name": "Khoury's Corner",
                "store_phone": "",
                "store_address": "",
                "exchange_rate": "not a number",
                "tax_rate": "0",
                "require_shift": True,
            })
        self.assertNotEqual(settings_service.get("store_name"), "Khoury's Corner")
