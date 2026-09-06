"""Store settings, currency configuration and account tools (Admin only)."""

from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

from app import auth, config, db, printing
from app.money import D, fmt_lbp, fmt_usd, parse_amount, to_lbp
from app.services import audit as audit_service
from app.services import backups as backups_service
from app.services import settings as settings_service
from app.ui import background, theme
from app.ui.shell import PageHeader
from app.ui.widgets import (
    Card,
    DataTable,
    FormModal,
    LabeledEntry,
    Modal,
    SectionTitle,
    ask_confirm,
    debounce,
    show_error,
    show_info,
)

APPEARANCES = ("System", "Light", "Dark")
SYSTEM_PRINTER = "System default"
RECEIPT_WIDTH_LABELS = {"58": "58 mm roll", "80": "80 mm roll"}


def _trim(value) -> str:
    """Render a Decimal without a trailing '.0' — but never eat integer zeros,
    which would turn an exchange rate of 95000 into 95."""
    text = f"{value:f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


class SettingsView(ctk.CTkScrollableFrame):
    def __init__(self, parent, shell):
        super().__init__(parent, fg_color="transparent")
        self.shell = shell
        self.grid_columnconfigure(0, weight=1)

        header = PageHeader(self, "Settings", "Store details, currency and appearance")
        header.grid(row=0, column=0, sticky="ew", padx=24, pady=(20, 14))

        self._build_store_card()
        self._build_currency_card()
        self._build_printing_card()
        self._build_security_card()
        self._build_data_card()
        self._build_account_card()

    # ------------------------------------------------------------------ #

    def _build_store_card(self) -> None:
        card = Card(self)
        card.grid(row=1, column=0, sticky="ew", padx=24)
        card.grid_columnconfigure((0, 1), weight=1)

        SectionTitle(card, "Store details").grid(
            row=0, column=0, columnspan=2, sticky="ew", padx=16, pady=(14, 2)
        )
        ctk.CTkLabel(
            card, text="Printed at the top of every receipt.",
            font=theme.font(11), text_color=theme.TEXT_MUTED, anchor="w",
        ).grid(row=1, column=0, columnspan=2, sticky="ew", padx=16, pady=(0, 10))

        self.store_name = LabeledEntry(card, "Store name")
        self.store_phone = LabeledEntry(card, "Phone")
        self.store_address = LabeledEntry(card, "Address")
        self.store_footer = LabeledEntry(card, "Receipt footer message")

        self.store_name.grid(row=2, column=0, sticky="ew", padx=16, pady=6)
        self.store_phone.grid(row=2, column=1, sticky="ew", padx=16, pady=6)
        self.store_address.grid(row=3, column=0, sticky="ew", padx=16, pady=6)
        self.store_footer.grid(row=3, column=1, sticky="ew", padx=16, pady=6)

        ctk.CTkButton(
            card, text="Save store details", height=38, width=180,
            command=self._save_store,
        ).grid(row=4, column=0, sticky="w", padx=16, pady=(10, 16))

    def _build_currency_card(self) -> None:
        card = Card(self)
        card.grid(row=2, column=0, sticky="ew", padx=24, pady=(16, 0))
        card.grid_columnconfigure((0, 1, 2), weight=1)

        SectionTitle(card, "Currency & tax").grid(
            row=0, column=0, columnspan=3, sticky="ew", padx=16, pady=(14, 2)
        )
        ctk.CTkLabel(
            card,
            text="Prices are kept in USD. Every sale stores the rate used at the time, "
                 "so reprinting an old receipt never re-prices it.",
            font=theme.font(11), text_color=theme.TEXT_MUTED, anchor="w",
            wraplength=820, justify="left",
        ).grid(row=1, column=0, columnspan=3, sticky="ew", padx=16, pady=(0, 10))

        self.rate = LabeledEntry(card, "Exchange rate (LBP per 1 USD)")
        self.tax = LabeledEntry(card, "Tax rate (%)")
        self.rounding = LabeledEntry(card, "Round LBP totals to nearest")
        self.rate.grid(row=2, column=0, sticky="ew", padx=16, pady=6)
        self.tax.grid(row=2, column=1, sticky="ew", padx=16, pady=6)
        self.rounding.grid(row=2, column=2, sticky="ew", padx=16, pady=6)
        for entry in (self.rate, self.tax, self.rounding):
            entry.variable.trace_add("write", lambda *_: self._update_preview())

        self.low_stock = LabeledEntry(card, "Default reorder level for new products")
        self.low_stock.grid(row=3, column=0, sticky="ew", padx=16, pady=6)

        self.preview = ctk.CTkLabel(
            card, text="", font=theme.font(12), text_color=theme.TEXT_MUTED, anchor="w"
        )
        self.preview.grid(row=4, column=0, columnspan=3, sticky="ew", padx=16, pady=(8, 0))

        ctk.CTkButton(
            card, text="Save currency & tax", height=38, width=180,
            command=self._save_currency,
        ).grid(row=5, column=0, sticky="w", padx=16, pady=(10, 16))

    def _build_printing_card(self) -> None:
        card = Card(self)
        card.grid(row=3, column=0, sticky="ew", padx=24, pady=(16, 0))
        card.grid_columnconfigure((0, 1), weight=1)

        SectionTitle(card, "Printing").grid(
            row=0, column=0, columnspan=2, sticky="ew", padx=16, pady=(14, 2)
        )
        ctk.CTkLabel(
            card,
            text=(
                "Choose a printer and a receipt is sent to it straight after a "
                "sale. Leave it on the system default to open the PDF instead."
            ),
            font=theme.font(11), text_color=theme.TEXT_MUTED, anchor="w",
            wraplength=820, justify="left",
        ).grid(row=1, column=0, columnspan=2, sticky="ew", padx=16, pady=(0, 10))

        printer_cell = ctk.CTkFrame(card, fg_color="transparent")
        printer_cell.grid(row=2, column=0, sticky="ew", padx=16, pady=6)
        printer_cell.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            printer_cell, text="Receipt printer", font=theme.font(12),
            text_color=theme.TEXT_MUTED, anchor="w",
        ).grid(row=0, column=0, sticky="ew", pady=(0, 4))
        self.printer_var = ctk.StringVar(value=SYSTEM_PRINTER)
        self.printer_menu = ctk.CTkOptionMenu(
            printer_cell, variable=self.printer_var, values=[SYSTEM_PRINTER], height=34
        )
        self.printer_menu.grid(row=1, column=0, sticky="ew")

        width_cell = ctk.CTkFrame(card, fg_color="transparent")
        width_cell.grid(row=2, column=1, sticky="ew", padx=16, pady=6)
        width_cell.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            width_cell, text="Receipt width", font=theme.font(12),
            text_color=theme.TEXT_MUTED, anchor="w",
        ).grid(row=0, column=0, sticky="ew", pady=(0, 4))
        self.width_var = ctk.StringVar(value=RECEIPT_WIDTH_LABELS["80"])
        ctk.CTkOptionMenu(
            width_cell, variable=self.width_var,
            values=list(RECEIPT_WIDTH_LABELS.values()), height=34,
        ).grid(row=1, column=0, sticky="ew")

        self.require_shift_check = ctk.CTkCheckBox(
            card, text="A till shift must be open before selling"
        )
        self.require_shift_check.grid(row=3, column=0, sticky="w", padx=16, pady=(8, 0))
        self.price_override_check = ctk.CTkCheckBox(
            card, text="Allow price overrides at the till"
        )
        self.price_override_check.grid(row=3, column=1, sticky="w", padx=16, pady=(8, 0))

        buttons = ctk.CTkFrame(card, fg_color="transparent")
        buttons.grid(row=4, column=0, columnspan=2, sticky="ew", padx=16, pady=(12, 16))
        ctk.CTkButton(
            buttons, text="Save printing settings", height=38, width=190,
            command=self._save_printing,
        ).grid(row=0, column=0, padx=(0, 8))
        ctk.CTkButton(
            buttons, text="Find printers", height=38, width=150,
            fg_color=theme.NEUTRAL, hover_color=theme.NEUTRAL_HOVER,
            command=self._find_printers,
        ).grid(row=0, column=1)

    def _build_security_card(self) -> None:
        card = Card(self)
        card.grid(row=4, column=0, sticky="ew", padx=24, pady=(16, 0))
        card.grid_columnconfigure((0, 1, 2), weight=1)

        SectionTitle(card, "Security").grid(
            row=0, column=0, columnspan=3, sticky="ew", padx=16, pady=(14, 2)
        )
        ctk.CTkLabel(
            card,
            text=(
                "A till stands on a counter all day. Locking the screen protects "
                "an unattended one without throwing away a half-built sale, and "
                "throttling stops a short password being guessed at leisure. "
                "Set either to 0 to switch it off."
            ),
            font=theme.font(11), text_color=theme.TEXT_MUTED, anchor="w",
            wraplength=820, justify="left",
        ).grid(row=1, column=0, columnspan=3, sticky="ew", padx=16, pady=(0, 10))

        self.idle_lock = LabeledEntry(card, "Lock the screen after (minutes idle)")
        self.max_attempts = LabeledEntry(card, "Failed sign-ins before locking")
        self.lockout_minutes = LabeledEntry(card, "Keep locked for (minutes)")
        self.idle_lock.grid(row=2, column=0, sticky="ew", padx=16, pady=6)
        self.max_attempts.grid(row=2, column=1, sticky="ew", padx=16, pady=6)
        self.lockout_minutes.grid(row=2, column=2, sticky="ew", padx=16, pady=6)

        self.locked_label = ctk.CTkLabel(
            card, text="", font=theme.font(11), text_color=theme.TEXT_MUTED, anchor="w",
        )
        self.locked_label.grid(row=3, column=0, columnspan=3, sticky="ew", padx=16, pady=(8, 0))

        buttons = ctk.CTkFrame(card, fg_color="transparent")
        buttons.grid(row=4, column=0, columnspan=3, sticky="ew", padx=16, pady=(10, 16))
        ctk.CTkButton(
            buttons, text="Save security settings", height=38, width=200,
            command=self._save_security,
        ).grid(row=0, column=0, padx=(0, 8))
        ctk.CTkButton(
            buttons, text="Unlock all accounts", height=38, width=180,
            fg_color=theme.NEUTRAL, hover_color=theme.NEUTRAL_HOVER,
            command=self._unlock_accounts,
        ).grid(row=0, column=1, padx=(0, 8))
        ctk.CTkButton(
            buttons, text="Lock the screen now", height=38, width=180,
            fg_color=theme.NEUTRAL, hover_color=theme.NEUTRAL_HOVER,
            command=self._lock_now,
        ).grid(row=0, column=2)

    def _save_security(self) -> None:
        try:
            idle = int(parse_amount(self.idle_lock.get() or 0, "idle lock"))
            attempts = int(parse_amount(self.max_attempts.get() or 0, "failed sign-ins"))
            minutes = int(parse_amount(self.lockout_minutes.get() or 0, "lockout"))
        except ValueError as exc:
            show_error(self, exc, "Invalid value")
            return
        if attempts == 1:
            show_error(
                self,
                "Locking after a single wrong password would lock somebody out "
                "for one mistyped character. Use 3 or more, or 0 to switch "
                "throttling off.",
                "Too strict",
            )
            return
        settings_service.set_many({
            "idle_lock_minutes": str(idle),
            "login_max_attempts": str(attempts),
            "login_lockout_minutes": str(minutes),
        })
        show_info(self, "Security settings saved.", "Settings saved")
        self._refresh_locked_label()

    def _unlock_accounts(self) -> None:
        locked = auth.locked_accounts()
        if not locked:
            show_info(self, "No accounts are locked.", "Nothing to unlock")
            return
        names = ", ".join(row["username"] for row in locked)
        if not ask_confirm(self, f"Unlock {names}?", "Unlock accounts"):
            return
        # One commit rather than one per account: an unlock of several accounts
        # that died halfway would otherwise leave the rest still locked.
        with db.transaction():
            for row in locked:
                auth.clear_lockout(row["username"])
        audit_service.record("Accounts unlocked", "user", "", names)
        self._refresh_locked_label()
        show_info(self, f"Unlocked: {names}", "Accounts unlocked")

    def _lock_now(self) -> None:
        root = self.winfo_toplevel()
        if hasattr(root, "lock_screen"):
            root.lock_screen("Locked from Settings.")

    def _refresh_locked_label(self) -> None:
        locked = auth.locked_accounts()
        if not locked:
            self.locked_label.configure(
                text="No accounts are locked out.", text_color=theme.TEXT_MUTED
            )
            return
        self.locked_label.configure(
            text="Locked out: "
                 + ", ".join(f"{row['username']} (until {row['locked_until']})"
                             for row in locked),
            text_color=theme.WARNING,
        )

    def _build_data_card(self) -> None:
        card = Card(self)
        card.grid(row=5, column=0, sticky="ew", padx=24, pady=(16, 0))
        card.grid_columnconfigure(0, weight=1)

        SectionTitle(card, "Data safety").grid(
            row=0, column=0, sticky="ew", padx=16, pady=(14, 2)
        )
        ctk.CTkLabel(
            card,
            text=(
                "Backups are taken with SQLite's own snapshot, so one can be made "
                "while the shop is trading. Restoring keeps a copy of the current "
                "data first, so it can always be undone."
            ),
            font=theme.font(11), text_color=theme.TEXT_MUTED, anchor="w",
            wraplength=820, justify="left",
        ).grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 10))

        options = ctk.CTkFrame(card, fg_color="transparent")
        options.grid(row=2, column=0, sticky="ew", padx=16)
        options.grid_columnconfigure(2, weight=1)

        self.backup_start_check = ctk.CTkCheckBox(
            options, text="Back up automatically at start-up",
            command=self._save_backup_options,
        )
        self.backup_start_check.grid(row=0, column=0, sticky="w", padx=(0, 16))
        self.backup_close_check = ctk.CTkCheckBox(
            options, text="Back up automatically at closing",
            command=self._save_backup_options,
        )
        self.backup_close_check.grid(row=1, column=0, sticky="w", pady=(4, 0))
        ctk.CTkLabel(
            options, text="Keep", font=theme.font(12), text_color=theme.TEXT_MUTED,
        ).grid(row=0, column=1, sticky="w", padx=(0, 6))
        self.backup_keep_entry = ctk.CTkEntry(options, width=70, height=32)
        self.backup_keep_entry.grid(row=0, column=2, sticky="w")

        buttons = ctk.CTkFrame(card, fg_color="transparent")
        buttons.grid(row=3, column=0, sticky="ew", padx=16, pady=(12, 16))
        for column, (label, command, colour) in enumerate((
            ("Back up now", self._backup_now, theme.PRIMARY),
            ("Restore a backup", self._restore, theme.DANGER),
            ("Check the database", self._integrity_check, theme.NEUTRAL),
            ("Audit log", self._open_audit, theme.NEUTRAL),
        )):
            ctk.CTkButton(
                buttons, text=label, height=38, width=160, command=command,
                fg_color=colour,
                hover_color=(
                    theme.DANGER_HOVER if colour == theme.DANGER
                    else theme.PRIMARY_HOVER if colour == theme.PRIMARY
                    else theme.NEUTRAL_HOVER
                ),
            ).grid(row=0, column=column, padx=(0, 8))

        self.backup_label = ctk.CTkLabel(
            card, text="", font=theme.font(11), text_color=theme.TEXT_MUTED, anchor="w",
        )
        self.backup_label.grid(row=4, column=0, sticky="ew", padx=16, pady=(0, 16))

        SectionTitle(card, "Keeping and tidying").grid(
            row=5, column=0, sticky="ew", padx=16, pady=(0, 2)
        )
        ctk.CTkLabel(
            card,
            text=(
                "Scratch work is tidied at start-up. Records are kept for ever "
                "unless you say otherwise — 0 means never delete."
            ),
            font=theme.font(11), text_color=theme.TEXT_MUTED, anchor="w",
            wraplength=820, justify="left",
        ).grid(row=6, column=0, sticky="ew", padx=16, pady=(0, 10))

        keep_row = ctk.CTkFrame(card, fg_color="transparent")
        keep_row.grid(row=7, column=0, sticky="ew", padx=16)
        self._keep_entries = {}
        for column, (key, label) in enumerate((
            ("parked_keep_days", "Held sales kept (days)"),
            ("receipt_keep_days", "Receipts kept (days)"),
            ("audit_keep_days", "Audit log kept (days)"),
        )):
            ctk.CTkLabel(
                keep_row, text=label, font=theme.font(12),
                text_color=theme.TEXT_MUTED,
            ).grid(row=0, column=column * 2, sticky="w", padx=(0, 6))
            entry = ctk.CTkEntry(keep_row, width=70, height=32)
            entry.grid(row=0, column=column * 2 + 1,
                       sticky="w", padx=(0, 16 if column < 2 else 0))
            self._keep_entries[key] = entry

        ctk.CTkButton(
            card, text="Save tidying", height=34, width=130,
            fg_color=theme.NEUTRAL, hover_color=theme.NEUTRAL_HOVER,
            command=self._save_retention,
        ).grid(row=8, column=0, sticky="w", padx=16, pady=(10, 16))

    def _build_account_card(self) -> None:
        card = Card(self)
        card.grid(row=6, column=0, sticky="ew", padx=24, pady=(16, 24))
        card.grid_columnconfigure(0, weight=1)

        SectionTitle(card, "Application").grid(
            row=0, column=0, sticky="ew", padx=16, pady=(14, 10)
        )

        appearance_row = ctk.CTkFrame(card, fg_color="transparent")
        appearance_row.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 10))
        ctk.CTkLabel(
            appearance_row, text="Appearance", font=theme.font(12),
            text_color=theme.TEXT_MUTED, width=180, anchor="w",
        ).grid(row=0, column=0, sticky="w")
        self.appearance_var = ctk.StringVar(value=ctk.get_appearance_mode())
        ctk.CTkOptionMenu(
            appearance_row, variable=self.appearance_var, values=list(APPEARANCES),
            width=160, height=34, command=self._change_appearance,
        ).grid(row=0, column=1, sticky="w")

        buttons = ctk.CTkFrame(card, fg_color="transparent")
        buttons.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 10))
        ctk.CTkButton(
            buttons, text="Change my password", height=38, width=190,
            command=self._change_password,
        ).grid(row=0, column=0, padx=(0, 8))
        ctk.CTkButton(
            buttons, text="Load demo products", height=38, width=180,
            fg_color=theme.NEUTRAL, hover_color=theme.NEUTRAL_HOVER,
            command=self._load_demo,
        ).grid(row=0, column=1)

        self.paths_label = ctk.CTkLabel(
            card, text="", font=theme.font(11), text_color=theme.TEXT_MUTED,
            anchor="w", justify="left",
        )
        self.paths_label.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 16))

    # ------------------------------------------------------------------ #

    def _update_preview(self) -> None:
        try:
            rate = parse_amount(self.rate.get(), "exchange rate")
            step = parse_amount(self.rounding.get() or 1, "rounding")
        except ValueError:
            self.preview.configure(text="")
            return
        if rate <= 0:
            self.preview.configure(text="")
            return
        sample = D("10")
        self.preview.configure(
            text=f"Preview:  {fmt_usd(sample)}  =  {fmt_lbp(to_lbp(sample, rate, step or 1))}"
        )

    def _save_store(self) -> None:
        settings_service.set_many({
            "store_name": self.store_name.get().strip(),
            "store_phone": self.store_phone.get().strip(),
            "store_address": self.store_address.get().strip(),
            "store_footer": self.store_footer.get().strip(),
        })
        show_info(self, "Store details saved.", "Settings saved")

    def _save_currency(self) -> None:
        try:
            rate = parse_amount(self.rate.get(), "exchange rate")
            tax = parse_amount(self.tax.get(), "tax rate")
            rounding = parse_amount(self.rounding.get() or 1, "rounding")
            low_stock = parse_amount(self.low_stock.get() or 0, "reorder level")
        except ValueError as exc:
            show_error(self, exc, "Invalid value")
            return
        if rate <= 0:
            show_error(self, "The exchange rate must be greater than zero.", "Invalid rate")
            return
        if tax > 100:
            show_error(self, "The tax rate cannot exceed 100%.", "Invalid tax rate")
            return

        settings_service.set_many({
            "exchange_rate": _trim(rate),
            "tax_rate": _trim(tax),
            "lbp_rounding": f"{rounding:.0f}",
            "low_stock_default": f"{low_stock:.0f}",
        })
        show_info(self, "Currency and tax settings saved.", "Settings saved")
        self.shell.invalidate("dashboard", "pos", "reports")

    # ------------------------------------------------------------------ #
    # Printing
    # ------------------------------------------------------------------ #

    def _find_printers(self) -> None:
        # Asking Windows for its printer list is a PowerShell subprocess that
        # can take the better part of half a minute on a sleepy machine; it
        # happens away from the UI thread.
        def job():
            return printing.list_printers()

        def done(names: list[str]) -> None:
            self.printer_menu.configure(values=[SYSTEM_PRINTER] + names)
            if names:
                show_info(self, f"Found {len(names)} printer(s).", "Printers")
            else:
                show_info(
                    self,
                    "No printers could be listed. You can still print through the "
                    "system default.",
                    "No printers found",
                )

        background.run(job, done, parent=self)

    def _save_printing(self) -> None:
        chosen = self.printer_var.get()
        width = next(
            (key for key, label in RECEIPT_WIDTH_LABELS.items()
             if label == self.width_var.get()),
            "80",
        )
        settings_service.set_many({
            "printer_name": "" if chosen == SYSTEM_PRINTER else chosen,
            "receipt_width_mm": width,
            "require_shift": "1" if self.require_shift_check.get() else "0",
            "allow_price_override": "1" if self.price_override_check.get() else "0",
        })
        show_info(self, "Printing settings saved.", "Settings saved")
        self.shell.invalidate("pos")

    # ------------------------------------------------------------------ #
    # Data safety
    # ------------------------------------------------------------------ #

    def _save_backup_options(self) -> None:
        try:
            keep = int(parse_amount(self.backup_keep_entry.get() or 20, "backups to keep"))
        except ValueError as exc:
            show_error(self, exc, "Invalid value")
            return
        settings_service.set_many({
            "backup_on_start": "1" if self.backup_start_check.get() else "0",
            "backup_on_close": "1" if self.backup_close_check.get() else "0",
            "backup_keep": str(max(keep, 1)),
        })

    def _save_retention(self) -> None:
        values = {}
        try:
            for key, entry in self._keep_entries.items():
                values[key] = str(max(0, int(parse_amount(entry.get() or "0", key))))
        except ValueError as exc:
            show_error(self, exc, "Invalid value")
            return
        settings_service.set_many(values)
        show_info(self, "Tidying rules saved. They take effect at the next start-up.",
                  "Saved")

    def _backup_now(self) -> None:
        self._save_backup_options()

        def job():
            path = backups_service.create("manual")
            backups_service.prune(settings_service.backup_keep())
            return path

        def done(path) -> None:
            self._refresh_backup_label()
            show_info(self, f"Backup written to:\n{path}", "Backup complete")

        def failed(exc: Exception) -> None:
            show_error(self, exc, "Backup failed")

        background.run(job, done, failed, parent=self)

    def _restore(self) -> None:
        entries = backups_service.list_backups()
        if not entries:
            show_error(self, "There are no backups to restore.", "No backups")
            return

        modal = RestoreModal(self, entries)
        path = modal.wait_result()
        if path is None:
            return
        if not ask_confirm(
            self,
            f"Replace all current data with {Path(path).name}?\n\n"
            f"A copy of the current data is saved first, so this can be undone.",
            "Restore backup",
        ):
            return
        # This one stays on the UI thread: restore closes and reopens *this*
        # thread's database connection, which no worker may do on its behalf.
        # The cursor says the window is busy for the second it takes.
        background.busy(self)
        try:
            safety = backups_service.restore(path)
        except backups_service.BackupError as exc:
            show_error(self, exc, "Restore failed")
            return
        finally:
            background.settled(self)

        self._refresh_backup_label()
        self.shell.invalidate(
            "dashboard", "pos", "till", "products", "purchasing", "customers",
            "invoices", "reports", "users",
        )
        show_info(
            self,
            f"Restored from {Path(path).name}.\n\n"
            f"The data as it was is saved at:\n{safety}",
            "Restore complete",
        )

    def _integrity_check(self) -> None:
        def job():
            return db.integrity_check()

        def done(detail: str) -> None:
            if detail.strip().lower() == "ok":
                show_info(
                    self,
                    "The database passed its integrity check.",
                    "Database checked",
                )
            else:
                show_error(
                    self,
                    f"The database reported a problem:\n\n{detail}\n\n"
                    f"Restore the most recent backup.",
                    "Database problem",
                )

        background.run(job, done, parent=self)

    def _open_audit(self) -> None:
        AuditModal(self)

    def _refresh_backup_label(self) -> None:
        entries = backups_service.list_backups()
        if not entries:
            self.backup_label.configure(
                text=f"No backups yet. They are kept in {config.BACKUPS_DIR}"
            )
            return
        newest = entries[0]
        self.backup_label.configure(
            text=(
                f"{len(entries)} backup(s) in {config.BACKUPS_DIR}\n"
                f"Most recent: {newest['name']} ({newest['taken_at']}, "
                f"{newest['size_kb']} kB)"
            )
        )

    def _change_appearance(self, mode: str) -> None:
        ctk.set_appearance_mode(mode)
        # Treeview styling is not managed by CustomTkinter, so rebuild the pages.
        self.shell.invalidate(
            "dashboard", "pos", "till", "products", "purchasing", "customers",
            "invoices", "reports", "users",
        )

    def _change_password(self) -> None:
        fields = [
            {"key": "current", "label": "Current password", "type": "password"},
            {"key": "new", "label": "New password", "type": "password",
             "hint": f"At least {auth.MIN_PASSWORD_LENGTH} characters."},
            {"key": "confirm", "label": "Confirm new password", "type": "password"},
        ]

        def submit(values):
            if values["new"] != values["confirm"]:
                raise auth.AuthError("The two new passwords do not match.")
            auth.change_password(
                self.shell.user.user_id, values["current"], values["new"]
            )

        if FormModal(self, "Change my password", fields, submit,
                     submit_text="Update password").wait_result():
            show_info(self, "Your password has been updated.", "Password changed")

    def _load_demo(self) -> None:
        if not ask_confirm(
            self,
            "Add a set of demo categories and products?\n\n"
            "This only runs while the catalogue is empty, and it does not touch "
            "existing data.",
            "Load demo products",
        ):
            return
        before = db.scalar("SELECT COUNT(*) FROM products", default=0)
        db.seed_demo_data()
        after = db.scalar("SELECT COUNT(*) FROM products", default=0)
        if after == before:
            show_info(
                self,
                "The catalogue already has products, so nothing was added.",
                "Nothing to do",
            )
        else:
            show_info(self, f"{after - before} demo products added.", "Demo data loaded")
            self.shell.invalidate("dashboard", "pos", "products")

    # ------------------------------------------------------------------ #

    def refresh(self) -> None:
        values = settings_service.get_all()
        self.store_name.set(values.get("store_name", ""))
        self.store_phone.set(values.get("store_phone", ""))
        self.store_address.set(values.get("store_address", ""))
        self.store_footer.set(values.get("store_footer", ""))
        self.rate.set(values.get("exchange_rate", ""))
        self.tax.set(values.get("tax_rate", ""))
        self.rounding.set(values.get("lbp_rounding", ""))
        self.low_stock.set(values.get("low_stock_default", ""))
        self.idle_lock.set(values.get("idle_lock_minutes", "0"))
        self.max_attempts.set(values.get("login_max_attempts", "0"))
        self.lockout_minutes.set(values.get("login_lockout_minutes", "0"))
        self._refresh_locked_label()
        self.appearance_var.set(ctk.get_appearance_mode())

        printer = values.get("printer_name", "")
        self.printer_menu.configure(
            values=[SYSTEM_PRINTER] + ([printer] if printer else [])
        )
        self.printer_var.set(printer or SYSTEM_PRINTER)
        self.width_var.set(
            RECEIPT_WIDTH_LABELS.get(values.get("receipt_width_mm", "80"),
                                     RECEIPT_WIDTH_LABELS["80"])
        )
        _set_check(self.require_shift_check, settings_service.require_shift())
        _set_check(self.price_override_check, settings_service.allow_price_override())
        _set_check(self.backup_start_check, settings_service.backup_on_start())
        _set_check(self.backup_close_check, settings_service.backup_on_close())
        self.backup_keep_entry.delete(0, "end")
        self.backup_keep_entry.insert(0, str(settings_service.backup_keep()))
        for key, entry in self._keep_entries.items():
            value = settings_service.get(key, config.DEFAULT_SETTINGS.get(key, "0"))
            entry.delete(0, "end")
            entry.insert(0, str(max(0, int(value or 0))))
        self._refresh_backup_label()

        self._update_preview()
        self.paths_label.configure(
            text=f"Database:  {db.database_path()}\n"
                 f"Receipts:  {config.RECEIPTS_DIR}\n"
                 f"Backups:   {config.BACKUPS_DIR}\n"
                 f"Images:    {config.IMAGES_DIR}\n"
                 f"Logs:      {config.LOGS_DIR}\n"
                 f"{config.APP_NAME} v{config.APP_VERSION}"
        )


def _set_check(widget, on: bool) -> None:
    widget.select() if on else widget.deselect()


class RestoreModal(Modal):
    """Pick which snapshot to go back to."""

    def __init__(self, parent, entries):
        super().__init__(parent, "Restore a backup", 620, 440)
        self.entries = entries
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        SectionTitle(self, "Available backups").grid(
            row=0, column=0, sticky="ew", padx=18, pady=(18, 8)
        )
        self.table = DataTable(
            self,
            columns=[
                ("name", "Backup", 300, "w"),
                ("taken_at", "Taken", 150, "w"),
                ("size_kb", "Size (kB)", 90, "e"),
            ],
            id_key="name",
            height=10,
        )
        self.table.grid(row=1, column=0, sticky="nsew", padx=18, pady=(0, 10))
        self.table.set_rows(
            entries,
            empty_message="No backups yet. Take one now — it takes a second.",
        )
        self.table.select_first()
        self.table.on_double_click(self.submit)

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=2, column=0, sticky="ew", padx=18, pady=(0, 16))
        footer.grid_columnconfigure(0, weight=1)
        ctk.CTkButton(
            footer, text="Cancel", width=100, height=36,
            fg_color=theme.NEUTRAL, hover_color=theme.NEUTRAL_HOVER,
            command=self.on_cancel,
        ).grid(row=0, column=1, padx=(0, 8))
        ctk.CTkButton(
            footer, text="Restore", width=140, height=36,
            fg_color=theme.DANGER, hover_color=theme.DANGER_HOVER,
            command=self.submit,
        ).grid(row=0, column=2)

    def submit(self) -> None:
        name = self.table.selected_id()
        if name is None:
            return
        entry = next((e for e in self.entries if e["name"] == name), None)
        if entry is None:
            return
        self.result = entry["path"]
        self.grab_release()
        self.destroy()


class AuditModal(Modal):
    """Who did what, and when."""

    def __init__(self, parent):
        super().__init__(parent, "Audit log", 900, 560)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        SectionTitle(self, "Audit log").grid(
            row=0, column=0, sticky="ew", padx=18, pady=(18, 8)
        )

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=1, column=0, sticky="ew", padx=18, pady=(0, 8))
        bar.grid_columnconfigure(0, weight=1)

        self.search = ctk.CTkEntry(
            bar, placeholder_text="Search by user, detail or record", height=34
        )
        self.search.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.search.bind("<KeyRelease>", debounce(self, 250, self.reload))

        self.action = ctk.CTkOptionMenu(
            bar, values=["All"] + audit_service.known_actions(), width=200, height=34,
            command=lambda _value: self.reload(),
        )
        self.action.set("All")
        self.action.grid(row=0, column=1)

        ctk.CTkButton(
            bar, text="Export CSV", width=120, height=34,
            fg_color=theme.NEUTRAL, hover_color=theme.NEUTRAL_HOVER,
            command=self._export,
        ).grid(row=0, column=2, padx=(8, 0))

        self.table = DataTable(
            self,
            columns=[
                ("at", "When", 150, "w"),
                ("username", "Who", 110, "w"),
                ("action", "Action", 160, "w"),
                ("entity", "Record", 100, "w"),
                ("entity_id", "Id", 60, "w"),
                ("detail", "Detail", 300, "w"),
            ],
            id_key="audit_id",
            height=14,
        )
        self.table.grid(row=2, column=0, sticky="nsew", padx=18, pady=(0, 10))

        ctk.CTkButton(
            self, text="Close", height=36, width=120, command=self.on_cancel
        ).grid(row=3, column=0, pady=(0, 16))

        self.reload()

    def reload(self) -> None:
        self.table.set_rows(
            audit_service.list_entries(
                search=self.search.get(), action=self.action.get()
            ),
            tag_func=lambda row: "danger" if "failed" in row["action"].lower() else (),
            empty_message="Nothing has been recorded yet.",
        )

    def _export(self) -> None:
        rows = audit_service.list_entries(
            search=self.search.get(), action=self.action.get(), limit=100000
        )
        if not rows:
            show_error(self, "There are no lines to export.", "Nothing to export")
            return
        target = filedialog.asksaveasfilename(
            parent=self,
            title="Export audit log to CSV",
            defaultextension=".csv",
            initialfile=f"re4-audit-{dt.date.today():%Y%m%d}.csv",
            filetypes=[("CSV file", "*.csv")],
        )
        if not target:
            return
        try:
            with open(target, "w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.writer(handle)
                writer.writerow(["When", "Who", "Action", "Record", "Id", "Detail"])
                for row in rows:
                    writer.writerow([
                        row["at"], row["username"], row["action"],
                        row["entity"], row["entity_id"], row["detail"],
                    ])
        except OSError as exc:
            show_error(self, exc, "Could not write the file")
            return
        show_info(self, f"{len(rows)} line(s) exported to:\n{target}", "Export complete")
