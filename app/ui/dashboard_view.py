"""Dashboard: today at a glance, a 7-day trend, low stock and recent sales."""

from __future__ import annotations

import datetime as dt
import tkinter as tk

import customtkinter as ctk

from app.money import D, fmt_lbp, fmt_usd, to_lbp
from app.services import products as products_service
from app.services import reports as reports_service
from app.services import sales as sales_service
from app.services import settings as settings_service
from app.ui import theme
from app.ui.shell import PageHeader
from app.ui.widgets import Card, DataTable, SectionTitle, StatCard


class BarChart(ctk.CTkFrame):
    """Minimal bar chart on a Tk canvas — no plotting dependency needed."""

    def __init__(self, parent, height: int = 190):
        super().__init__(parent, fg_color="transparent")
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(
            self, height=height, highlightthickness=0, bd=0,
            bg=theme.pick(theme.SURFACE),
        )
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self._data: list[tuple[str, float]] = []
        self.canvas.bind("<Configure>", lambda _event: self._render())

    def set_data(self, data) -> None:
        self._data = list(data)
        self._render()

    def _render(self) -> None:
        canvas = self.canvas
        canvas.delete("all")
        canvas.configure(bg=theme.pick(theme.SURFACE))

        width = canvas.winfo_width() or 480
        height = canvas.winfo_height() or 190
        if not self._data:
            canvas.create_text(
                width / 2, height / 2, text="No sales in this period",
                fill=theme.pick(theme.TEXT_MUTED), font=("Segoe UI", 10),
            )
            return

        pad_x, pad_top, pad_bottom = 12, 18, 26
        plot_height = max(height - pad_top - pad_bottom, 20)
        peak = max((value for _label, value in self._data), default=0) or 1
        slot = (width - 2 * pad_x) / len(self._data)
        bar_width = max(min(slot * 0.55, 46), 6)
        fill = theme.pick(theme.PRIMARY)
        muted = theme.pick(theme.TEXT_MUTED)

        for index, (label, value) in enumerate(self._data):
            centre = pad_x + slot * (index + 0.5)
            bar_height = max((value / peak) * plot_height, 2 if value else 0)
            top = pad_top + plot_height - bar_height
            canvas.create_rectangle(
                centre - bar_width / 2, top, centre + bar_width / 2,
                pad_top + plot_height, fill=fill, outline="",
            )
            if value:
                canvas.create_text(
                    centre, top - 8, text=f"{value:,.0f}",
                    fill=muted, font=("Segoe UI", 8),
                )
            canvas.create_text(
                centre, height - pad_bottom / 2, text=label,
                fill=muted, font=("Segoe UI", 8),
            )


class DashboardView(ctk.CTkScrollableFrame):
    def __init__(self, parent, shell):
        super().__init__(parent, fg_color="transparent")
        self.shell = shell
        self.grid_columnconfigure(0, weight=1)

        self.header = PageHeader(self, "Dashboard", "")
        self.header.grid(row=0, column=0, sticky="ew", padx=24, pady=(20, 16))

        ctk.CTkButton(
            self.header.actions, text="New Sale", height=38, width=130,
            font=theme.font(13, "bold"),
            command=lambda: self.shell.show("pos"),
        ).grid(row=0, column=0)

        # -- stat cards ----------------------------------------------------- #
        stats = ctk.CTkFrame(self, fg_color="transparent")
        stats.grid(row=1, column=0, sticky="ew", padx=24)
        for column in range(4):
            stats.grid_columnconfigure(column, weight=1, uniform="stat")

        self.card_today = StatCard(stats, "Revenue today", accent=theme.PRIMARY)
        self.card_month = StatCard(stats, "Revenue this month", accent=theme.SUCCESS)
        self.card_profit = StatCard(stats, "Gross profit (month)")
        self.card_stock = StatCard(stats, "Stock at cost")
        for column, card in enumerate(
            (self.card_today, self.card_month, self.card_profit, self.card_stock)
        ):
            card.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else 12, 0))

        # -- trend + low stock ---------------------------------------------- #
        middle = ctk.CTkFrame(self, fg_color="transparent")
        middle.grid(row=2, column=0, sticky="ew", padx=24, pady=(16, 0))
        middle.grid_columnconfigure(0, weight=3, uniform="mid")
        middle.grid_columnconfigure(1, weight=2, uniform="mid")

        chart_card = Card(middle)
        chart_card.grid(row=0, column=0, sticky="nsew")
        chart_card.grid_columnconfigure(0, weight=1)
        SectionTitle(chart_card, "Revenue — last 7 days").grid(
            row=0, column=0, sticky="ew", padx=16, pady=(14, 0)
        )
        self.chart = BarChart(chart_card)
        self.chart.grid(row=1, column=0, sticky="nsew", padx=10, pady=(6, 12))

        low_card = Card(middle)
        low_card.grid(row=0, column=1, sticky="nsew", padx=(12, 0))
        low_card.grid_rowconfigure(1, weight=1)
        low_card.grid_columnconfigure(0, weight=1)
        SectionTitle(low_card, "Needs restocking").grid(
            row=0, column=0, sticky="ew", padx=16, pady=(14, 8)
        )
        self.low_stock = DataTable(
            low_card,
            columns=[
                ("name", "Product", 180, "w"),
                ("stock_qty", "In stock", 70, "center"),
                ("reorder_level", "Reorder at", 80, "center"),
            ],
            id_key="product_id",
            height=6,
            border_width=0,
            fg_color="transparent",
        )
        self.low_stock.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 10))
        self.low_stock.on_double_click(lambda: self.shell.show("products"))

        # -- recent sales ---------------------------------------------------- #
        recent_card = Card(self)
        recent_card.grid(row=3, column=0, sticky="ew", padx=24, pady=(16, 24))
        recent_card.grid_columnconfigure(0, weight=1)
        SectionTitle(recent_card, "Recent sales").grid(
            row=0, column=0, sticky="ew", padx=16, pady=(14, 8)
        )
        self.recent = DataTable(
            recent_card,
            columns=[
                ("invoice_no", "Invoice", 150, "w"),
                ("sale_time", "When", 150, "w"),
                ("customer_name", "Customer", 180, "w"),
                ("item_count", "Items", 70, "center"),
                ("total_usd", "Total", 110, "e"),
                ("status", "Status", 100, "center"),
            ],
            id_key="sale_id",
            height=8,
            border_width=0,
            fg_color="transparent",
        )
        self.recent.set_formatter("total_usd", lambda value, _row: fmt_usd(value))
        self.recent.set_formatter(
            "customer_name", lambda value, _row: value or "Walk-in"
        )
        self.recent.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 10))
        self.recent.on_double_click(lambda: self.shell.show("invoices"))

    def refresh(self) -> None:
        today = reports_service.today()
        rate = settings_service.exchange_rate()
        rounding = settings_service.lbp_rounding()

        day = reports_service.summary(today, today)
        month = reports_service.summary(reports_service.month_start(), today)
        stock = reports_service.inventory_snapshot()

        self.card_today.set(
            fmt_usd(day["revenue"]),
            f"{day['sale_count']} sales · {fmt_lbp(to_lbp(day['revenue'], rate, rounding))}",
        )
        self.card_month.set(
            fmt_usd(month["revenue"]),
            f"{month['sale_count']} sales · avg {fmt_usd(month['average_sale'])}",
        )
        self.card_profit.set(
            fmt_usd(month["gross_profit"]),
            f"{month['units']} units sold"
            + (f" · {month['refund_count']} refunded" if month["refund_count"] else ""),
        )
        self.card_stock.set(
            fmt_usd(stock.get("stock_cost", 0)),
            f"{stock.get('product_count', 0)} products · "
            f"{stock.get('low_stock_count', 0)} low · "
            f"{stock.get('out_of_stock_count', 0)} out",
        )

        series = {
            row["day"]: row["revenue"]
            for row in reports_service.daily_series(reports_service.days_ago(6), today)
        }
        points = []
        for offset in range(6, -1, -1):
            date = dt.date.today() - dt.timedelta(days=offset)
            points.append((date.strftime("%a"), float(series.get(date.isoformat(), 0))))
        self.chart.set_data(points)

        low = products_service.low_stock_products()
        self.low_stock.set_rows(
            low,
            tag_func=lambda row: "danger" if row["stock_qty"] == 0 else "warning",
            empty_message="Every product is above its reorder level.",
        )

        self.recent.set_rows(
            sales_service.list_sales(limit=12),
            tag_func=lambda row: "muted" if row["status"] != "Completed" else (),
            empty_message="No sales recorded yet — start one from New Sale.",
        )
        self.header.set_subtitle(
            f"{dt.date.today():%A, %d %B %Y}   ·   1 USD = {D(rate):,.0f} LBP"
        )
