"""Invoice history: look up past sales, reprint receipts, process refunds."""

from __future__ import annotations

import customtkinter as ctk

from app import config
from app.money import D, fmt_lbp, fmt_usd, to_lbp
from app.services import sales as sales_service
from app.services import reports as reports_service
from app.services import settings as settings_service
from app.ui import theme
from app.ui.receipt_actions import print_receipt, save_receipt_as
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

RANGES = ("Today", "Last 7 days", "This month", "All time")
STATUSES = ("All", config.SALE_COMPLETED, config.SALE_REFUNDED)


def range_dates(label: str):
    if label == "Today":
        return reports_service.today(), reports_service.today()
    if label == "Last 7 days":
        return reports_service.days_ago(6), reports_service.today()
    if label == "This month":
        return reports_service.month_start(), reports_service.today()
    return None, None


class InvoicesView(ctk.CTkFrame):
    def __init__(self, parent, shell):
        super().__init__(parent, fg_color="transparent")
        self.shell = shell

        self.grid_rowconfigure(2, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self.header = PageHeader(self, "Invoices", "")
        self.header.grid(row=0, column=0, sticky="ew", padx=24, pady=(20, 14))

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=1, column=0, sticky="ew", padx=24, pady=(0, 10))
        bar.grid_columnconfigure(0, weight=1)

        self.search_var = ctk.StringVar()
        self.search_var.trace_add("write", lambda *_: self.refresh())
        ctk.CTkEntry(
            bar, textvariable=self.search_var, height=36,
            placeholder_text="Search by invoice number, customer or cashier…",
        ).grid(row=0, column=0, sticky="ew")

        self.range_var = ctk.StringVar(value="Last 7 days")
        ctk.CTkOptionMenu(
            bar, variable=self.range_var, values=list(RANGES), width=150, height=36,
            command=lambda _value: self.refresh(),
        ).grid(row=0, column=1, padx=(8, 0))

        self.status_var = ctk.StringVar(value="All")
        ctk.CTkOptionMenu(
            bar, variable=self.status_var, values=list(STATUSES), width=130, height=36,
            command=lambda _value: self.refresh(),
        ).grid(row=0, column=2, padx=(8, 0))

        card = Card(self)
        card.grid(row=2, column=0, sticky="nsew", padx=24, pady=(0, 12))
        card.grid_rowconfigure(0, weight=1)
        card.grid_columnconfigure(0, weight=1)

        self.table = DataTable(
            card,
            columns=[
                ("invoice_no", "Invoice", 160, "w"),
                ("sale_time", "When", 150, "w"),
                ("customer_name", "Customer", 180, "w"),
                ("cashier", "Cashier", 110, "w"),
                ("item_count", "Items", 60, "center"),
                ("payment_method", "Payment", 110, "w"),
                ("total_usd", "Total", 110, "e"),
                ("status", "Status", 100, "center"),
            ],
            id_key="sale_id",
            height=16,
            border_width=0,
            fg_color="transparent",
        )
        self.table.set_formatter("total_usd", lambda value, _row: fmt_usd(value))
        self.table.set_formatter("customer_name", lambda value, _row: value or "Walk-in")
        self.table.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        self.table.on_double_click(self._details)

        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.grid(row=3, column=0, sticky="ew", padx=24, pady=(0, 20))

        specs = [
            ("View details", self._details, theme.PRIMARY),
            ("Print receipt", self._print, theme.NEUTRAL),
            ("Save PDF…", self._save_pdf, theme.NEUTRAL),
        ]
        if shell.user.is_admin:
            specs.append(("Refund", self._refund, theme.DANGER))
        for column, (label, command, colour) in enumerate(specs):
            ctk.CTkButton(
                buttons, text=label, height=36, width=140, command=command,
                fg_color=colour,
                hover_color=(
                    theme.DANGER_HOVER if colour == theme.DANGER
                    else theme.PRIMARY_HOVER if colour == theme.PRIMARY
                    else theme.NEUTRAL_HOVER
                ),
            ).grid(row=0, column=column, padx=(0, 8))

    # ------------------------------------------------------------------ #

    def _selected_id(self) -> int | None:
        sale_id = self.table.selected_int()
        if sale_id is None:
            show_error(self, "Select an invoice first.", "Nothing selected")
        return sale_id

    def _details(self) -> None:
        sale_id = self._selected_id()
        if sale_id is not None:
            InvoiceDetailModal(self, sale_id)

    def _print(self) -> None:
        sale_id = self._selected_id()
        if sale_id is not None:
            print_receipt(self, sale_id)

    def _save_pdf(self) -> None:
        sale_id = self._selected_id()
        if sale_id is not None:
            save_receipt_as(self, sale_id)

    def _refund(self) -> None:
        sale_id = self._selected_id()
        if sale_id is None:
            return
        sale = sales_service.get_sale(sale_id)
        if sale["status"] == config.SALE_REFUNDED:
            show_error(self, f"{sale['invoice_no']} is already refunded.", "Already refunded")
            return
        if not ask_confirm(
            self,
            f"Refund {sale['invoice_no']} for {fmt_usd(sale['total_usd'])}?\n\n"
            f"The items go back into stock and the sale stops counting towards revenue.",
            "Refund invoice",
        ):
            return

        dialog = FormModal(
            self, "Refund reason",
            [{"key": "reason", "label": "Reason (optional)", "value": ""}],
            submit_text="Refund",
        )
        values = dialog.wait_result()
        if values is None:
            return
        try:
            sales_service.refund_sale(sale_id, self.shell.user.user_id, values["reason"])
        except sales_service.SaleError as exc:
            show_error(self, exc, "Refund failed")
            return
        self.refresh()
        self.shell.invalidate("dashboard", "products", "reports")

    def refresh(self) -> None:
        date_from, date_to = range_dates(self.range_var.get())
        status = self.status_var.get()
        rows = sales_service.list_sales(
            date_from=date_from,
            date_to=date_to,
            search=self.search_var.get(),
            status=None if status == "All" else status,
        )
        self.table.set_rows(
            rows,
            tag_func=lambda row: "muted" if row["status"] != config.SALE_COMPLETED else (),
            empty_message="No invoices in this range.",
        )
        total = sum(
            row["total_usd"] for row in rows if row["status"] == config.SALE_COMPLETED
        )
        self.header.set_subtitle(
            f"{len(rows)} invoices  ·  {fmt_usd(total)} in completed sales"
        )


class InvoiceDetailModal(Modal):
    def __init__(self, parent, sale_id: int):
        sale = sales_service.get_sale(sale_id)
        super().__init__(parent, f"Invoice {sale['invoice_no']}", 620, 560)
        self.sale_id = sale_id
        self.grid_rowconfigure(2, weight=1)
        self.grid_columnconfigure(0, weight=1)

        rate = D(sale["exchange_rate"]) or settings_service.exchange_rate()
        rounding = settings_service.lbp_rounding()

        SectionTitle(self, sale["invoice_no"]).grid(
            row=0, column=0, sticky="ew", padx=18, pady=(18, 2)
        )

        meta = ctk.CTkFrame(self, fg_color="transparent")
        meta.grid(row=1, column=0, sticky="ew", padx=18, pady=(0, 10))
        meta.grid_columnconfigure((1, 3), weight=1)
        pairs = [
            ("Date", str(sale["sale_time"])),
            ("Status", sale["status"]),
            ("Customer", sale["customer_name"] or "Walk-in"),
            ("Cashier", sale["cashier_name"] or sale["cashier"] or "—"),
            ("Payment", sale["payment_method"]),
            ("Paid in", sale["paid_currency"]),
        ]
        for index, (label, value) in enumerate(pairs):
            row, column = divmod(index, 2)
            ctk.CTkLabel(
                meta, text=label, font=theme.font(11), text_color=theme.TEXT_MUTED,
                anchor="w",
            ).grid(row=row, column=column * 2, sticky="w", padx=(0, 8), pady=1)
            ctk.CTkLabel(
                meta, text=value, font=theme.font(12, "bold"), text_color=theme.TEXT,
                anchor="w",
            ).grid(row=row, column=column * 2 + 1, sticky="w", padx=(0, 20), pady=1)

        table = DataTable(
            self,
            columns=[
                ("name_at_sale", "Item", 230, "w"),
                ("sku_at_sale", "SKU", 100, "w"),
                ("qty", "Qty", 55, "center"),
                ("unit_price_usd", "Unit", 90, "e"),
                ("line_total_usd", "Total", 95, "e"),
            ],
            id_key="sale_item_id",
            height=8,
        )
        table.set_formatter("unit_price_usd", lambda value, _row: fmt_usd(value))
        table.set_formatter("line_total_usd", lambda value, _row: fmt_usd(value))
        table.grid(row=2, column=0, sticky="nsew", padx=18, pady=(0, 10))
        table.set_rows(sales_service.get_sale_items(sale_id))

        totals = Card(self, fg_color=theme.SURFACE_ALT, border_width=0)
        totals.grid(row=3, column=0, sticky="ew", padx=18, pady=(0, 12))
        totals.grid_columnconfigure(1, weight=1)

        lines = [("Subtotal", fmt_usd(sale["subtotal_usd"]))]
        if D(sale["discount_usd"]) > 0:
            lines.append(("Discount", f"-{fmt_usd(sale['discount_usd'])}"))
        if D(sale["tax_usd"]) > 0:
            lines.append(("Tax", fmt_usd(sale["tax_usd"])))
        lines.append(("TOTAL", fmt_usd(sale["total_usd"])))
        lines.append(("Total in LBP", fmt_lbp(to_lbp(sale["total_usd"], rate, rounding))))
        if D(sale["change_usd"]) > 0:
            lines.append(("Change given", fmt_usd(sale["change_usd"])))

        for index, (label, value) in enumerate(lines):
            bold = label.startswith("TOTAL")
            ctk.CTkLabel(
                totals, text=label, font=theme.font(13 if bold else 12, "bold" if bold else "normal"),
                text_color=theme.TEXT_MUTED, anchor="w",
            ).grid(row=index, column=0, sticky="w", padx=(14, 0), pady=(8 if index == 0 else 1, 0))
            ctk.CTkLabel(
                totals, text=value,
                font=theme.font(15 if bold else 12, "bold" if bold else "normal"),
                text_color=theme.TEXT if bold else theme.TEXT_MUTED, anchor="e",
            ).grid(
                row=index, column=1, sticky="e", padx=(0, 14),
                pady=(8 if index == 0 else 1, 10 if index == len(lines) - 1 else 0),
            )

        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.grid(row=4, column=0, sticky="ew", padx=18, pady=(0, 16))
        buttons.grid_columnconfigure((0, 1, 2), weight=1)
        ctk.CTkButton(
            buttons, text="Print receipt", height=36,
            command=lambda: print_receipt(self, sale_id),
        ).grid(row=0, column=0, padx=3, sticky="ew")
        ctk.CTkButton(
            buttons, text="Save PDF…", height=36, fg_color=theme.NEUTRAL,
            hover_color=theme.NEUTRAL_HOVER,
            command=lambda: save_receipt_as(self, sale_id),
        ).grid(row=0, column=1, padx=3, sticky="ew")
        ctk.CTkButton(
            buttons, text="Close", height=36, fg_color=theme.NEUTRAL,
            hover_color=theme.NEUTRAL_HOVER, command=self.on_cancel,
        ).grid(row=0, column=2, padx=3, sticky="ew")
