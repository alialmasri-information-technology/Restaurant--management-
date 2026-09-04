"""Reports: revenue, margin, best sellers and CSV export."""

from __future__ import annotations

import csv
import datetime as dt
from tkinter import filedialog

import customtkinter as ctk

from app.money import D, fmt_lbp, fmt_usd, to_lbp
from app.services import accounts as accounts_service
from app.services import reports as reports_service
from app.services import sales as sales_service
from app.services import settings as settings_service
from app.ui import receipt_actions, theme
from app.ui.dashboard_view import BarChart
from app.ui.shell import PageHeader
from app.ui.widgets import Card, DataTable, SectionTitle, StatCard, show_error, show_info

RANGES = ("Today", "Last 7 days", "Last 30 days", "This month", "This year", "All time")


def range_dates(label: str):
    today = dt.date.today()
    if label == "Today":
        return today.isoformat(), today.isoformat()
    if label == "Last 7 days":
        return (today - dt.timedelta(days=6)).isoformat(), today.isoformat()
    if label == "Last 30 days":
        return (today - dt.timedelta(days=29)).isoformat(), today.isoformat()
    if label == "This month":
        return today.replace(day=1).isoformat(), today.isoformat()
    if label == "This year":
        return today.replace(month=1, day=1).isoformat(), today.isoformat()
    return None, None


class ReportsView(ctk.CTkScrollableFrame):
    def __init__(self, parent, shell):
        super().__init__(parent, fg_color="transparent")
        self.shell = shell
        self.grid_columnconfigure(0, weight=1)

        self.header = PageHeader(self, "Reports", "")
        self.header.grid(row=0, column=0, sticky="ew", padx=24, pady=(20, 14))

        self.range_var = ctk.StringVar(value="Last 30 days")
        ctk.CTkOptionMenu(
            self.header.actions, variable=self.range_var, values=list(RANGES),
            width=160, height=36, command=lambda _value: self.refresh(),
        ).grid(row=0, column=0, padx=(0, 8))
        ctk.CTkButton(
            self.header.actions, text="Day report", height=36, width=120,
            fg_color=theme.NEUTRAL, hover_color=theme.NEUTRAL_HOVER,
            command=self._day_report,
        ).grid(row=0, column=1, padx=(0, 8))
        ctk.CTkButton(
            self.header.actions, text="Export CSV", height=36, width=120,
            fg_color=theme.NEUTRAL, hover_color=theme.NEUTRAL_HOVER,
            command=self._export_csv,
        ).grid(row=0, column=2)

        stats = ctk.CTkFrame(self, fg_color="transparent")
        stats.grid(row=1, column=0, sticky="ew", padx=24)
        for column in range(4):
            stats.grid_columnconfigure(column, weight=1, uniform="stat")

        self.card_revenue = StatCard(stats, "Revenue", accent=theme.PRIMARY)
        self.card_profit = StatCard(stats, "Gross profit", accent=theme.SUCCESS)
        self.card_sales = StatCard(stats, "Sales")
        self.card_refunds = StatCard(stats, "Refunds", accent=theme.DANGER)
        for column, card in enumerate(
            (self.card_revenue, self.card_profit, self.card_sales, self.card_refunds)
        ):
            card.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else 12, 0))

        chart_card = Card(self)
        chart_card.grid(row=2, column=0, sticky="ew", padx=24, pady=(16, 0))
        chart_card.grid_columnconfigure(0, weight=1)
        SectionTitle(chart_card, "Revenue by day").grid(
            row=0, column=0, sticky="ew", padx=16, pady=(14, 0)
        )
        self.chart = BarChart(chart_card, height=200)
        self.chart.grid(row=1, column=0, sticky="ew", padx=10, pady=(6, 12))

        grid = ctk.CTkFrame(self, fg_color="transparent")
        grid.grid(row=3, column=0, sticky="ew", padx=24, pady=(16, 24))
        grid.grid_columnconfigure((0, 1), weight=1, uniform="reports")

        self.top_products = self._table_card(
            grid, 0, 0, "Best sellers",
            [("name", "Product", 200, "w"), ("units", "Units", 70, "center"),
             ("revenue", "Revenue", 110, "e")],
            "name",
        )
        self.top_customers = self._table_card(
            grid, 0, 1, "Top customers",
            [("name", "Customer", 200, "w"), ("sale_count", "Sales", 70, "center"),
             ("revenue", "Revenue", 110, "e")],
            "name",
        )
        self.by_payment = self._table_card(
            grid, 1, 0, "Payment methods",
            [("payment_method", "Method", 200, "w"), ("sale_count", "Sales", 70, "center"),
             ("revenue", "Revenue", 110, "e")],
            "payment_method",
        )
        self.by_user = self._table_card(
            grid, 1, 1, "Sales by user",
            [("name", "User", 200, "w"), ("sale_count", "Sales", 70, "center"),
             ("revenue", "Revenue", 110, "e")],
            "name",
        )
        # Receivables are not filtered by the date range: what is owed is owed
        # today regardless of which month it was rung up in.
        self.owed = self._table_card(
            grid, 2, 0, "Owed to you",
            [("name", "Customer", 180, "w"), ("phone", "Phone", 110, "w"),
             ("oldest_charge", "Oldest charge", 120, "w"),
             ("balance_usd", "Balance", 100, "e")],
            "customer_id",
        )
        self.owed.set_formatter("balance_usd", lambda value, _row: fmt_usd(value))
        self.owed.set_formatter(
            "oldest_charge", lambda value, _row: (value or "")[:10] or "—"
        )
        self.owed.set_formatter("phone", lambda value, _row: value or "—")

    def _table_card(self, parent, row, column, title, columns, id_key) -> DataTable:
        card = Card(parent)
        card.grid(
            row=row, column=column, sticky="nsew",
            padx=(0 if column == 0 else 12, 0), pady=(0 if row == 0 else 16, 0),
        )
        card.grid_rowconfigure(1, weight=1)
        card.grid_columnconfigure(0, weight=1)
        SectionTitle(card, title).grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 8))

        table = DataTable(
            card, columns=columns, id_key=id_key, height=8,
            border_width=0, fg_color="transparent",
        )
        table.set_formatter("revenue", lambda value, _row: fmt_usd(value))
        table.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 10))
        return table

    # ------------------------------------------------------------------ #

    def _day_report(self) -> None:
        # The last day of the chosen range, so "Yesterday" prints yesterday and
        # any longer range prints the day it ended on.
        _, date_to = range_dates(self.range_var.get())
        receipt_actions.print_day_report(self, date_to or reports_service.today())

    def _export_csv(self) -> None:
        date_from, date_to = range_dates(self.range_var.get())
        rows = sales_service.list_sales(date_from=date_from, date_to=date_to, limit=100000)
        if not rows:
            show_error(self, "There are no sales in this range to export.", "Nothing to export")
            return

        target = filedialog.asksaveasfilename(
            parent=self,
            title="Export sales to CSV",
            defaultextension=".csv",
            initialfile=f"re4-sales-{dt.date.today():%Y%m%d}.csv",
            filetypes=[("CSV file", "*.csv")],
        )
        if not target:
            return

        rate = settings_service.exchange_rate()
        rounding = settings_service.lbp_rounding()
        try:
            with open(target, "w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.writer(handle)
                writer.writerow([
                    "Invoice", "Date", "Customer", "Cashier", "Items", "Subtotal USD",
                    "Discount USD", "Tax USD", "Total USD", "Total LBP",
                    "Payment method", "Status",
                ])
                for row in rows:
                    writer.writerow([
                        row["invoice_no"],
                        row["sale_time"],
                        row["customer_name"] or "Walk-in",
                        row["cashier"] or "",
                        row["item_count"],
                        f"{row['subtotal_usd']:.2f}",
                        f"{row['discount_usd']:.2f}",
                        f"{row['tax_usd']:.2f}",
                        f"{row['total_usd']:.2f}",
                        f"{to_lbp(row['total_usd'], D(row['exchange_rate']) or rate, rounding):.0f}",
                        row["payment_method"],
                        row["status"],
                    ])
        except OSError as exc:
            show_error(self, exc, "Could not write the file")
            return
        show_info(self, f"{len(rows)} invoices exported to:\n{target}", "Export complete")

    def refresh(self) -> None:
        date_from, date_to = range_dates(self.range_var.get())
        summary = reports_service.summary(date_from, date_to)
        rate = settings_service.exchange_rate()
        rounding = settings_service.lbp_rounding()

        margin = (
            summary["gross_profit"] / summary["revenue"] * 100
            if summary["revenue"] else 0
        )
        self.card_revenue.set(
            fmt_usd(summary["revenue"]),
            fmt_lbp(to_lbp(summary["revenue"], rate, rounding)),
        )
        self.card_profit.set(fmt_usd(summary["gross_profit"]), f"{margin:.1f}% margin")
        self.card_sales.set(
            str(summary["sale_count"]),
            f"{summary['units']} units · avg {fmt_usd(summary['average_sale'])}",
        )
        self.card_refunds.set(
            str(summary["refund_count"]), fmt_usd(summary["refund_total"]) + " refunded"
        )

        start = date_from or reports_service.days_ago(29)
        end = date_to or reports_service.today()
        series = reports_service.daily_series(start, end)
        points = [
            (dt.date.fromisoformat(row["day"]).strftime("%d/%m"), float(row["revenue"]))
            for row in series
        ][-31:]
        self.chart.set_data(points)

        self.top_products.set_rows(reports_service.top_products(date_from, date_to))
        self.top_customers.set_rows(
            reports_service.top_customers(date_from, date_to),
            empty_message="No sales linked to a customer yet — add one at the till.",
        )
        self.by_payment.set_rows(reports_service.payment_breakdown(date_from, date_to))
        self.by_user.set_rows(reports_service.sales_by_user(date_from, date_to))

        owing = accounts_service.outstanding()
        receivable = accounts_service.total_receivable()
        self.owed.set_rows(
            owing,
            tag_func=lambda row: (
                "danger"
                if row["credit_limit_usd"]
                and row["balance_usd"] >= row["credit_limit_usd"] - 0.005
                else "warning"
            ),
            empty_message="Nobody owes you anything. Every account is settled.",
        )

        label = self.range_var.get().lower()
        subtitle = f"{label} · {date_from or 'start'} → {date_to or 'today'}"
        if receivable:
            subtitle += (
                f"   ·   {fmt_usd(receivable)} owed on account "
                f"by {len(owing)} customer(s)"
            )
        self.header.set_subtitle(subtitle)
