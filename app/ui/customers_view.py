"""Customer directory."""

from __future__ import annotations

import customtkinter as ctk

from app import config
from app.money import fmt_usd, parse_amount
from app.services import accounts as accounts_service
from app.services import customers as customers_service
from app.ui import phrasing, theme
from app.ui.shell import PageHeader
from app.ui.widgets import (
    Card,
    DataTable,
    FormModal,
    Modal,
    SectionTitle,
    ask_confirm,
    show_error,
    show_info,
)


class CustomersView(ctk.CTkFrame):
    def __init__(self, parent, shell):
        super().__init__(parent, fg_color="transparent")
        self.shell = shell

        self.grid_rowconfigure(2, weight=1)
        self.grid_columnconfigure(0, weight=1)

        header = PageHeader(self, "Customers", "Contact details and purchase history")
        header.grid(row=0, column=0, sticky="ew", padx=24, pady=(20, 14))
        ctk.CTkButton(
            header.actions, text="+ Add customer", height=36, width=150,
            font=theme.font(13, "bold"), command=self._add,
        ).grid(row=0, column=0)

        search_row = ctk.CTkFrame(self, fg_color="transparent")
        search_row.grid(row=1, column=0, sticky="ew", padx=24, pady=(0, 10))
        search_row.grid_columnconfigure(0, weight=1)

        self.search_var = ctk.StringVar()
        self.search_var.trace_add("write", lambda *_: self.refresh())
        ctk.CTkEntry(
            search_row, textvariable=self.search_var, height=36,
            placeholder_text="Search by name, phone or email…",
        ).grid(row=0, column=0, sticky="ew", padx=(0, 10))

        self.owing_only = ctk.CTkCheckBox(
            search_row, text="Owing only", command=self.refresh
        )
        self.owing_only.grid(row=0, column=1, padx=(0, 12))

        self.receivable_label = ctk.CTkLabel(
            search_row, text="", font=theme.font(12, "bold"),
            text_color=theme.TEXT_MUTED,
        )
        self.receivable_label.grid(row=0, column=2)

        card = Card(self)
        card.grid(row=2, column=0, sticky="nsew", padx=24, pady=(0, 12))
        card.grid_rowconfigure(0, weight=1)
        card.grid_columnconfigure(0, weight=1)

        self.table = DataTable(
            card,
            columns=[
                ("name", "Name", 220, "w"),
                ("phone", "Phone", 130, "w"),
                ("email", "Email", 200, "w"),
                ("purchase_count", "Purchases", 90, "center"),
                ("total_spent_usd", "Total spent", 120, "e"),
                ("balance_usd", "Owes", 110, "e"),
                ("credit_limit_usd", "Limit", 100, "e"),
                ("last_purchase", "Last purchase", 150, "w"),
            ],
            id_key="customer_id",
            height=16,
            border_width=0,
            fg_color="transparent",
        )
        self.table.set_formatter("total_spent_usd", lambda value, _row: fmt_usd(value))
        self.table.set_formatter(
            "balance_usd", lambda value, _row: fmt_usd(value) if value else "—"
        )
        self.table.set_formatter(
            "credit_limit_usd", lambda value, _row: fmt_usd(value) if value else "—"
        )
        self.table.set_formatter(
            "last_purchase", lambda value, _row: phrasing.day_label(value, empty="Never")
        )
        self.table.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        self.table.on_double_click(self._edit)

        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.grid(row=3, column=0, sticky="ew", padx=24, pady=(0, 20))
        for column, (label, command, colour) in enumerate((
            ("Edit", self._edit, theme.NEUTRAL),
            ("Take a payment", self._take_payment, theme.SUCCESS),
            ("Statement", self._statement, theme.PRIMARY),
            ("Credit limit", self._set_limit, theme.NEUTRAL),
            ("Purchase history", self._history, theme.NEUTRAL),
            ("Delete", self._delete, theme.DANGER),
        )):
            ctk.CTkButton(
                buttons, text=label, height=36, width=150, command=command,
                fg_color=colour,
                hover_color=(
                    theme.DANGER_HOVER if colour == theme.DANGER
                    else theme.PRIMARY_HOVER if colour == theme.PRIMARY
                    else theme.SUCCESS_HOVER if colour == theme.SUCCESS
                    else theme.NEUTRAL_HOVER
                ),
            ).grid(row=0, column=column, padx=(0, 8))

    # ------------------------------------------------------------------ #

    def _fields(self, customer=None) -> list[dict]:
        return [
            {"key": "name", "label": "Name", "value": customer["name"] if customer else ""},
            {"key": "phone", "label": "Phone", "value": customer["phone"] if customer else ""},
            {"key": "email", "label": "Email", "value": customer["email"] if customer else ""},
            {"key": "address", "label": "Address", "type": "text",
             "value": customer["address"] if customer else ""},
            {"key": "notes", "label": "Notes", "type": "text",
             "value": customer["notes"] if customer else ""},
        ]

    def _selected(self):
        customer_id = self.table.selected_int()
        if customer_id is None:
            show_error(self, "Select a customer first.", "Nothing selected")
            return None
        return customers_service.get_customer(customer_id)

    def _add(self) -> None:
        dialog = FormModal(
            self, "Add customer", self._fields(),
            lambda values: customers_service.create_customer(**values),
            submit_text="Add customer",
        )
        if dialog.wait_result():
            self.refresh()
            self.shell.invalidate("pos")

    def _edit(self) -> None:
        customer = self._selected()
        if customer is None:
            return
        dialog = FormModal(
            self, f"Edit — {customer['name']}", self._fields(customer),
            lambda values: customers_service.update_customer(
                customer["customer_id"], **values
            ),
        )
        if dialog.wait_result():
            self.refresh()
            self.shell.invalidate("pos")

    def _delete(self) -> None:
        customer = self._selected()
        if customer is None:
            return
        if not ask_confirm(
            self,
            f"Delete {customer['name']}?\n\nPast invoices are kept and become "
            f"walk-in sales.",
            "Delete customer",
        ):
            return
        customers_service.delete_customer(customer["customer_id"])
        self.refresh()
        self.shell.invalidate("pos", "invoices")

    def _history(self) -> None:
        customer = self._selected()
        if customer is None:
            return
        CustomerHistoryModal(self, customer)

    # -- account -------------------------------------------------------- #

    def _take_payment(self) -> None:
        customer = self._selected()
        if customer is None:
            return
        owed = accounts_service.balance(customer["customer_id"])
        if owed <= 0:
            show_info(self, f"{customer['name']} does not owe anything.", "Nothing due")
            return

        fields = [
            {"key": "owed", "label": "Balance outstanding", "type": "readonly",
             "value": str(owed)},
            {"key": "amount", "label": "Amount received", "type": "number",
             "value": str(owed),
             "hint": "Defaults to the full balance. Enter less for a part payment."},
            {"key": "currency", "label": "Currency", "type": "option",
             "values": list(config.CURRENCIES)},
            {"key": "method", "label": "Method", "type": "option",
             "values": list(config.PAYMENT_METHODS)},
            {"key": "note", "label": "Note", "type": "entry"},
        ]

        def submit(values):
            accounts_service.record_payment(
                customer["customer_id"],
                parse_amount(values["amount"], "amount"),
                user_id=self.shell.user.user_id,
                method=values["method"],
                paid_currency=values["currency"],
                note=values["note"],
            )

        if FormModal(self, f"Payment — {customer['name']}", fields, submit,
                     submit_text="Record payment").wait_result():
            self.refresh()
            self.shell.invalidate("dashboard", "till", "reports")
            show_info(
                self,
                f"{customer['name']} now owes "
                f"{fmt_usd(accounts_service.balance(customer['customer_id']))}.",
                "Payment recorded",
            )

    def _statement(self) -> None:
        customer = self._selected()
        if customer is None:
            return
        StatementModal(self, customer, self.shell, on_change=self.refresh)

    def _set_limit(self) -> None:
        customer = self._selected()
        if customer is None:
            return
        fields = [
            {"key": "limit", "label": "Credit limit (USD)", "type": "number",
             "value": str(accounts_service.credit_limit(customer["customer_id"])),
             "hint": "0 means no credit at all — nothing can be put on account. "
                     "The limit is checked against the balance before each sale."},
        ]

        def submit(values):
            accounts_service.set_credit_limit(
                customer["customer_id"],
                parse_amount(values["limit"], "credit limit"),
                user_id=self.shell.user.user_id,
            )

        if FormModal(self, f"Credit limit — {customer['name']}", fields, submit,
                     submit_text="Save limit").wait_result():
            self.refresh()

    def refresh(self) -> None:
        self.table.set_rows(
            customers_service.list_customers(
                self.search_var.get(), owing_only=bool(self.owing_only.get())
            ),
            tag_func=_customer_tag,
            empty_message=(
                "Nobody owes anything." if self.owing_only.get()
                else "No customers yet — add one to track repeat business."
            ),
        )
        receivable = accounts_service.total_receivable()
        self.receivable_label.configure(
            text=f"Owed to you: {fmt_usd(receivable)}" if receivable else "",
            text_color=theme.WARNING if receivable else theme.TEXT_MUTED,
        )


def _customer_tag(row) -> str:
    balance = row["balance_usd"] or 0
    if balance <= 0.005:
        return ""
    limit = row["credit_limit_usd"] or 0
    # At or over the limit they cannot buy on account again, which is the thing
    # the person on the counter needs to see before they try.
    return "danger" if limit and balance >= limit - 0.005 else "warning"


class CustomerHistoryModal(Modal):
    def __init__(self, parent, customer):
        super().__init__(parent, f"Purchases — {customer['name']}", 620, 460)
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        SectionTitle(
            self,
            f"{customer['name']}  ·  {customer['purchase_count']} purchases  ·  "
            f"{fmt_usd(customer['total_spent_usd'])} lifetime",
        ).grid(row=0, column=0, sticky="ew", padx=18, pady=(18, 8))

        table = DataTable(
            self,
            columns=[
                ("invoice_no", "Invoice", 160, "w"),
                ("sale_time", "When", 160, "w"),
                ("total_usd", "Total", 110, "e"),
                ("status", "Status", 110, "center"),
            ],
            id_key="sale_id",
            height=12,
        )
        table.set_formatter("total_usd", lambda value, _row: fmt_usd(value))
        table.grid(row=1, column=0, sticky="nsew", padx=18, pady=(0, 12))
        table.set_rows(
            customers_service.purchase_history(customer["customer_id"]),
            tag_func=lambda row: "muted" if row["status"] != "Completed" else (),
            empty_message="Nothing bought yet — this will fill in after their first sale.",
        )
        ctk.CTkButton(self, text="Close", height=36, command=self.on_cancel).grid(
            row=2, column=0, pady=(0, 16)
        )


class StatementModal(Modal):
    """Every movement on one account, oldest first, with the running balance.

    This is the screen you open when a customer says "that is not what I owe".
    Each line names the invoice or return slip behind it, so the figure can be
    walked through rather than asserted.
    """

    def __init__(self, parent, customer, shell, on_change=None):
        super().__init__(parent, f"Account — {customer['name']}", 760, 520)
        self.customer = customer
        self.shell = shell
        self.on_change = on_change

        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self.heading = SectionTitle(self, "")
        self.heading.grid(row=0, column=0, sticky="ew", padx=18, pady=(18, 8))

        self.table = DataTable(
            self,
            columns=[
                ("at", "When", 140, "w"),
                ("kind", "What", 100, "w"),
                ("document", "Reference", 150, "w"),
                ("method", "Method", 90, "w"),
                ("amount_usd", "Amount", 100, "e"),
                ("running_balance", "Balance", 100, "e"),
            ],
            id_key="entry_id",
            height=13,
        )
        self.table.set_formatter("amount_usd", lambda value, _row: fmt_usd(value))
        self.table.set_formatter("running_balance", lambda value, _row: fmt_usd(value))
        self.table.set_formatter("document", lambda value, _row: value or "—")
        self.table.set_formatter("method", lambda value, _row: value or "—")
        self.table.grid(row=1, column=0, sticky="nsew", padx=18, pady=(0, 12))

        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.grid(row=2, column=0, sticky="ew", padx=18, pady=(0, 16))
        buttons.grid_columnconfigure(0, weight=1)
        ctk.CTkButton(
            buttons, text="Write off / adjust", width=170, height=36,
            fg_color=theme.NEUTRAL, hover_color=theme.NEUTRAL_HOVER,
            command=self._adjust,
        ).grid(row=0, column=1, padx=(0, 8))
        ctk.CTkButton(
            buttons, text="Close", width=120, height=36, command=self.on_cancel
        ).grid(row=0, column=2)

        self.reload()

    def reload(self) -> None:
        rows = accounts_service.statement(self.customer["customer_id"])
        owed = accounts_service.balance(self.customer["customer_id"])
        limit = accounts_service.credit_limit(self.customer["customer_id"])
        self.heading.configure(
            text=f"{self.customer['name']}   ·   owes {fmt_usd(owed)}"
                 + (f"   ·   limit {fmt_usd(limit)}" if limit else "   ·   no credit")
        )
        self.table.set_rows(
            rows,
            tag_func=lambda row: (
                "success" if row["amount_usd"] < 0 else "warning"
            ),
            empty_message="Nothing has ever gone on this account.",
        )
        if self.on_change is not None:
            self.on_change()

    def _adjust(self) -> None:
        if not self.shell.user.is_admin:
            show_error(
                self,
                "Only an administrator can write off or adjust a balance.",
                "Not permitted",
            )
            return

        fields = [
            {"key": "amount", "label": "Adjustment (USD)", "type": "entry",
             "hint": "Negative reduces what they owe — write off 40 with -40. "
                     "Positive adds a charge."},
            {"key": "reason", "label": "Reason", "type": "entry",
             "hint": "Recorded on the statement and in the audit log."},
        ]

        def submit(values):
            accounts_service.adjust(
                self.customer["customer_id"],
                values["amount"],
                user_id=self.shell.user.user_id,
                reason=values["reason"],
            )

        if FormModal(self, "Adjust the balance", fields, submit,
                     submit_text="Apply adjustment").wait_result():
            self.reload()
