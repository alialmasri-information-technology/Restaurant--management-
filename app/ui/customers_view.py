"""Customer directory."""

from __future__ import annotations

import customtkinter as ctk

from app.money import fmt_usd
from app.services import customers as customers_service
from app.ui import theme
from app.ui.shell import PageHeader
from app.ui.widgets import (
    Card,
    DataTable,
    FormModal,
    Modal,
    SectionTitle,
    ask_confirm,
    show_error,
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

        self.search_var = ctk.StringVar()
        self.search_var.trace_add("write", lambda *_: self.refresh())
        ctk.CTkEntry(
            self, textvariable=self.search_var, height=36,
            placeholder_text="Search by name, phone or email…",
        ).grid(row=1, column=0, sticky="ew", padx=24, pady=(0, 10))

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
                ("last_purchase", "Last purchase", 150, "w"),
            ],
            id_key="customer_id",
            height=16,
            border_width=0,
            fg_color="transparent",
        )
        self.table.set_formatter("total_spent_usd", lambda value, _row: fmt_usd(value))
        self.table.set_formatter("last_purchase", lambda value, _row: value or "—")
        self.table.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        self.table.on_double_click(self._edit)

        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.grid(row=3, column=0, sticky="ew", padx=24, pady=(0, 20))
        for column, (label, command, colour) in enumerate((
            ("Edit", self._edit, theme.NEUTRAL),
            ("Purchase history", self._history, theme.PRIMARY),
            ("Delete", self._delete, theme.DANGER),
        )):
            ctk.CTkButton(
                buttons, text=label, height=36, width=150, command=command,
                fg_color=colour,
                hover_color=(
                    theme.DANGER_HOVER if colour == theme.DANGER
                    else theme.PRIMARY_HOVER if colour == theme.PRIMARY
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

    def refresh(self) -> None:
        self.table.set_rows(
            customers_service.list_customers(self.search_var.get()),
            empty_message="No customers yet — add one to track repeat business.",
        )


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
            empty_message="This customer has no purchases yet.",
        )
        ctk.CTkButton(self, text="Close", height=36, command=self.on_cancel).grid(
            row=2, column=0, pady=(0, 16)
        )
