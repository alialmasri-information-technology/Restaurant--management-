"""Invoice history: look up past sales, reprint receipts, process refunds."""

from __future__ import annotations

import customtkinter as ctk

from app import config
from app.money import D, fmt_lbp, fmt_usd, parse_int, to_lbp
from app.services import reports as reports_service
from app.services import returns as returns_service
from app.services import sales as sales_service
from app.services import settings as settings_service
from app.ui import phrasing, theme
from app.ui.receipt_actions import print_receipt, print_return_slip, save_receipt_as
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
        self.table.set_formatter(
            "customer_name", lambda value, _row: phrasing.name_or(value)
        )
        # A cashier looking for "the one from about an hour ago" reads this faster
        # than a timestamp; anything older keeps its date.
        self.table.set_formatter(
            "sale_time", lambda value, _row: phrasing.relative_time(value)
        )
        self.table.set_formatter(
            "status", lambda _value, row: sales_service.display_status(row)
        )
        self.table.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        self.table.on_double_click(self._details)

        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.grid(row=3, column=0, sticky="ew", padx=24, pady=(0, 20))

        specs = [
            ("View details", self._details, theme.PRIMARY),
            ("Print receipt", self._print, theme.NEUTRAL),
            ("Save PDF…", self._save_pdf, theme.NEUTRAL),
            ("Return items…", self._return_items, theme.WARNING),
        ]
        if shell.user.is_admin:
            specs.append(("Refund all", self._refund, theme.DANGER))
        for column, (label, command, colour) in enumerate(specs):
            ctk.CTkButton(
                buttons, text=label, height=36, width=140, command=command,
                fg_color=colour,
                hover_color=(
                    theme.DANGER_HOVER if colour == theme.DANGER
                    else theme.PRIMARY_HOVER if colour == theme.PRIMARY
                    else theme.WARNING if colour == theme.WARNING
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

    def _return_items(self) -> None:
        sale_id = self._selected_id()
        if sale_id is None:
            return
        lines = returns_service.returnable_lines(sale_id)
        if not any(line["remaining_qty"] > 0 for line in lines):
            sale = sales_service.get_sale(sale_id)
            show_error(
                self,
                f"Everything on {sale['invoice_no']} has already been returned.",
                "Nothing to return",
            )
            return

        modal = ReturnModal(self, sale_id, self.shell.user)
        return_id = modal.wait_result()
        if return_id is None:
            return

        self.refresh()
        self.shell.invalidate("dashboard", "products", "reports", "till")
        record = returns_service.get_return(return_id)
        if ask_confirm(
            self,
            f"{record['return_no']} refunded {fmt_usd(record['total_usd'])}.\n\n"
            f"Print the return slip?",
            "Return recorded",
        ):
            print_return_slip(self, return_id)

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
        self.shell.invalidate("dashboard", "products", "reports", "till")

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
            empty_message="No invoices in this range. Try widening the dates.",
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


class ReturnModal(Modal):
    """Choose how many of each line comes back, showing the refund before it happens.

    The refund is quoted by the service rather than worked out here, so the
    figure on screen is exactly the figure that will be written.
    """

    def __init__(self, parent, sale_id: int, user):
        sale = sales_service.get_sale(sale_id)
        super().__init__(parent, f"Return against {sale['invoice_no']}", 660, 580)
        self.sale_id = sale_id
        self.user = user
        self.entries: dict[int, ctk.CTkEntry] = {}

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            self,
            text=f"Sold {sale['sale_time']} for {fmt_usd(sale['total_usd'])}",
            font=theme.font(12), text_color=theme.TEXT_MUTED, anchor="w",
        ).grid(row=0, column=0, sticky="ew", padx=18, pady=(16, 8))

        body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=18)
        body.grid_columnconfigure(0, weight=1)

        for column, text in enumerate(("Item", "Sold", "Already back", "Returning")):
            ctk.CTkLabel(
                body, text=text, font=theme.font(11, "bold"),
                text_color=theme.TEXT_MUTED, anchor="w",
            ).grid(row=0, column=column, sticky="w", padx=6, pady=(0, 6))

        for index, line in enumerate(returns_service.returnable_lines(sale_id), start=1):
            ctk.CTkLabel(
                body, text=line["name_at_sale"], font=theme.font(12), anchor="w",
            ).grid(row=index, column=0, sticky="ew", padx=6, pady=3)
            ctk.CTkLabel(
                body, text=str(line["qty"]), font=theme.font(12), anchor="e",
            ).grid(row=index, column=1, sticky="e", padx=6, pady=3)
            ctk.CTkLabel(
                body, text=str(line["returned_qty"]), font=theme.font(12), anchor="e",
            ).grid(row=index, column=2, sticky="e", padx=6, pady=3)

            entry = ctk.CTkEntry(body, width=80, height=32)
            entry.insert(0, "0")
            entry.grid(row=index, column=3, padx=6, pady=3)
            if line["remaining_qty"] <= 0:
                entry.configure(state="disabled")
            else:
                entry.bind("<KeyRelease>", lambda _event: self._requote())
            self.entries[line["sale_item_id"]] = entry

        options = ctk.CTkFrame(self, fg_color="transparent")
        options.grid(row=2, column=0, sticky="ew", padx=18, pady=(10, 0))
        options.grid_columnconfigure(2, weight=1)

        ctk.CTkLabel(
            options, text="Reason", font=theme.font(12), text_color=theme.TEXT_MUTED,
        ).grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.reason = ctk.CTkOptionMenu(
            options, values=list(config.RETURN_REASONS), width=180, height=34
        )
        self.reason.grid(row=0, column=1, sticky="w")

        ctk.CTkLabel(
            options, text="Refund by", font=theme.font(12), text_color=theme.TEXT_MUTED,
        ).grid(row=0, column=2, sticky="e", padx=(16, 8))
        self.method = ctk.CTkOptionMenu(
            options, values=list(config.PAYMENT_METHODS), width=150, height=34
        )
        self.method.set(sale["payment_method"])
        self.method.grid(row=0, column=3, sticky="e")

        self.restock = ctk.CTkCheckBox(self, text="Put the goods back into stock")
        self.restock.select()
        self.restock.grid(row=3, column=0, sticky="w", padx=18, pady=(10, 0))

        self.quote_label = ctk.CTkLabel(
            self, text="Refund $0.00", font=theme.font(18, "bold"),
            text_color=theme.TEXT, anchor="e",
        )
        self.quote_label.grid(row=4, column=0, sticky="ew", padx=18, pady=(8, 0))
        self.detail_label = ctk.CTkLabel(
            self, text="Enter the quantities coming back.", font=theme.font(11),
            text_color=theme.TEXT_MUTED, anchor="e",
        )
        self.detail_label.grid(row=5, column=0, sticky="ew", padx=18)

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=6, column=0, sticky="ew", padx=18, pady=(10, 16))
        footer.grid_columnconfigure(0, weight=1)
        ctk.CTkButton(
            footer, text="Return everything", width=150, height=36,
            fg_color=theme.NEUTRAL, hover_color=theme.NEUTRAL_HOVER,
            command=self._fill_all,
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(
            footer, text="Cancel", width=100, height=36,
            fg_color=theme.NEUTRAL, hover_color=theme.NEUTRAL_HOVER,
            command=self.on_cancel,
        ).grid(row=0, column=1, padx=(0, 8))
        ctk.CTkButton(
            footer, text="Refund", width=140, height=36, command=self.submit
        ).grid(row=0, column=2)

    # ------------------------------------------------------------------ #

    def _quantities(self) -> dict:
        quantities = {}
        for sale_item_id, entry in self.entries.items():
            text = entry.get().strip()
            if not text:
                continue
            quantities[sale_item_id] = parse_int(text, "quantity")
        return quantities

    def _fill_all(self) -> None:
        for line in returns_service.returnable_lines(self.sale_id):
            entry = self.entries[line["sale_item_id"]]
            if str(entry.cget("state")) == "disabled":
                continue
            entry.delete(0, "end")
            entry.insert(0, str(line["remaining_qty"]))
        self._requote()

    def _requote(self) -> None:
        """Show the live refund. A half-typed quantity is not an error yet."""
        try:
            quoted = returns_service.quote(self.sale_id, self._quantities())
        except (returns_service.ReturnError, ValueError) as exc:
            self.quote_label.configure(text="Refund $0.00")
            self.detail_label.configure(text=str(exc))
            return

        self.quote_label.configure(text=f"Refund {fmt_usd(quoted['total_usd'])}")
        parts = [f"goods {fmt_usd(quoted['line_value_usd'])}"]
        if quoted["discount_share_usd"]:
            parts.append(f"less discount {fmt_usd(quoted['discount_share_usd'])}")
        if quoted["tax_share_usd"]:
            parts.append(f"plus tax {fmt_usd(quoted['tax_share_usd'])}")
        self.detail_label.configure(text="   ".join(parts))

    def submit(self) -> None:
        try:
            quantities = self._quantities()
        except ValueError as exc:
            show_error(self, exc, "Check the quantities")
            return

        try:
            return_id = returns_service.create_return(
                self.sale_id, self.user.user_id, quantities,
                refund_method=self.method.get(),
                reason=self.reason.get(),
                restock=bool(self.restock.get()),
            )
        except returns_service.ReturnError as exc:
            show_error(self, exc, "Could not process the return")
            return

        self.result = return_id
        self.grab_release()
        self.destroy()
