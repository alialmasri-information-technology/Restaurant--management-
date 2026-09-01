"""Store settings, currency configuration and account tools (Admin only)."""

from __future__ import annotations

import customtkinter as ctk

from app import auth, config, db
from app.money import D, fmt_lbp, fmt_usd, parse_amount, to_lbp
from app.services import settings as settings_service
from app.ui import theme
from app.ui.shell import PageHeader
from app.ui.widgets import (
    Card,
    FormModal,
    LabeledEntry,
    SectionTitle,
    ask_confirm,
    show_error,
    show_info,
)

APPEARANCES = ("System", "Light", "Dark")


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

    def _build_account_card(self) -> None:
        card = Card(self)
        card.grid(row=3, column=0, sticky="ew", padx=24, pady=(16, 24))
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

    def _change_appearance(self, mode: str) -> None:
        ctk.set_appearance_mode(mode)
        # Treeview styling is not managed by CustomTkinter, so rebuild the pages.
        self.shell.invalidate(
            "dashboard", "pos", "products", "customers", "invoices", "reports", "users"
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
        self.appearance_var.set(ctk.get_appearance_mode())
        self._update_preview()
        self.paths_label.configure(
            text=f"Database:  {db.database_path()}\n"
                 f"Receipts:  {config.RECEIPTS_DIR}\n"
                 f"{config.APP_NAME} v{config.APP_VERSION}"
        )
