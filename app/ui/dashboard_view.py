"""Dashboard: what needs you, today at a glance, a trend, low stock, recent sales.

The briefing sits above the numbers on purpose. A revenue figure answers a
question somebody already has; a briefing tells the person who has just walked
in what they did not know to ask about.
"""

from __future__ import annotations

import datetime as dt
import tkinter as tk

import customtkinter as ctk

from app.money import D, fmt_lbp, fmt_usd, to_lbp
from app.services import briefing as briefing_service
from app.services import products as products_service
from app.services import reports as reports_service
from app.services import sales as sales_service
from app.services import settings as settings_service
from app.ui import phrasing, theme
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


#: Tone -> the dot that leads the line, and its colour.
TONE_MARKS = {
    briefing_service.WARN: ("●", theme.WARNING),
    briefing_service.NOTE: ("●", theme.PRIMARY),
    briefing_service.CALM: ("●", theme.SUCCESS),
}


class Briefing(Card):
    """The handful of things outstanding, each one a click from being dealt with."""

    def __init__(self, parent, shell):
        super().__init__(parent)
        self.shell = shell
        self.grid_columnconfigure(0, weight=1)
        self._rows: list[ctk.CTkBaseClass] = []

    def set_notes(self, notes) -> None:
        for widget in self._rows:
            widget.destroy()
        self._rows = []

        if not notes:
            self._add_line(
                "✓", theme.SUCCESS,
                "Nothing needs you right now — the shop is in good shape.", "",
                last=True,
            )
            return
        for index, note in enumerate(notes):
            mark, colour = TONE_MARKS.get(note.tone, TONE_MARKS[briefing_service.NOTE])
            self._add_line(
                mark, colour, note.text, note.screen, last=index == len(notes) - 1
            )

    def _add_line(
        self, mark: str, colour, text: str, screen: str, *, last: bool = False
    ) -> None:
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.grid(
            row=len(self._rows), column=0, sticky="ew", padx=14,
            pady=(12 if not self._rows else 2, 12 if last else 2),
        )
        row.grid_columnconfigure(1, weight=1)
        self._rows.append(row)

        ctk.CTkLabel(
            row, text=mark, font=theme.font(13, "bold"), text_color=colour, width=16,
        ).grid(row=0, column=0, sticky="w")

        # A note whose screen you cannot open is a label, not a link — promising
        # somewhere to go and then not going there is worse than plain text.
        if screen and screen in self.shell.nav_buttons:
            ctk.CTkButton(
                row, text=text, anchor="w", height=24, font=theme.font(13),
                fg_color="transparent", hover_color=theme.SURFACE_ALT,
                text_color=theme.TEXT, corner_radius=6,
                command=lambda: self.shell.show(screen),
            ).grid(row=0, column=1, sticky="ew", padx=(4, 0))
        else:
            ctk.CTkLabel(
                row, text=text, anchor="w", font=theme.font(13), text_color=theme.TEXT,
            ).grid(row=0, column=1, sticky="ew", padx=(8, 0))


def _trend(current, previous, _label: str = ""):
    """Render a percentage move as ``("▲ 12%", 1)``, or nothing without a baseline."""
    ratio = reports_service.change_ratio(current, previous)
    if ratio is None:
        return None
    if abs(ratio) < 0.005:
        return ("• level", 0)
    arrow = "▲" if ratio > 0 else "▼"
    return (f"{arrow} {abs(ratio) * 100:,.0f}%", 1 if ratio > 0 else -1)


class DashboardView(ctk.CTkScrollableFrame):
    def __init__(self, parent, shell):
        super().__init__(parent, fg_color="transparent")
        self.shell = shell
        self.grid_columnconfigure(0, weight=1)

        self.header = PageHeader(self, phrasing.greeting(shell.user.display_name), "")
        self.header.grid(row=0, column=0, sticky="ew", padx=24, pady=(20, 14))

        ctk.CTkButton(
            self.header.actions, text="New Sale", height=38, width=130,
            font=theme.font(13, "bold"),
            command=lambda: self.shell.show("pos"),
        ).grid(row=0, column=0)

        # -- what needs you -------------------------------------------------- #
        self.briefing = Briefing(self, shell)
        self.briefing.grid(row=1, column=0, sticky="ew", padx=24, pady=(0, 14))

        # -- stat cards ----------------------------------------------------- #
        stats = ctk.CTkFrame(self, fg_color="transparent")
        stats.grid(row=2, column=0, sticky="ew", padx=24)
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
        middle.grid(row=3, column=0, sticky="ew", padx=24, pady=(16, 0))
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
        recent_card.grid(row=4, column=0, sticky="ew", padx=24, pady=(16, 24))
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
            "customer_name", lambda value, _row: phrasing.name_or(value)
        )
        # "20 minutes ago" is what somebody glancing at the till wants; the exact
        # timestamp is still on the invoice, where it is needed.
        self.recent.set_formatter(
            "sale_time", lambda value, _row: phrasing.relative_time(value)
        )
        self.recent.set_formatter(
            "status", lambda value, _row: "Refunded" if value != "Completed" else "Done"
        )
        self.recent.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 10))
        self.recent.on_double_click(lambda: self.shell.show("invoices"))

    def refresh(self) -> None:
        self.header.set_title(phrasing.greeting(self.shell.user.display_name))
        self.briefing.set_notes(
            briefing_service.notes(is_admin=self.shell.user.is_admin)
        )

        today = reports_service.today()
        month_start = reports_service.month_start()
        rate = settings_service.exchange_rate()
        rounding = settings_service.lbp_rounding()

        day = reports_service.summary(today, today)
        month = reports_service.summary(month_start, today)
        stock = reports_service.inventory_snapshot()

        # Same-length windows ending the day before, so a comparison made on the
        # 3rd of the month is not measured against a full month.
        yesterday = reports_service.summary(*reports_service.previous_period(today, today))
        last_month = reports_service.summary(
            *reports_service.previous_period(month_start, today)
        )

        self.card_today.set(
            fmt_usd(day["revenue"]),
            f"{day['sale_count']} sales · {fmt_lbp(to_lbp(day['revenue'], rate, rounding))}",
            trend=_trend(day["revenue"], yesterday["revenue"], "yesterday"),
        )
        self.card_month.set(
            fmt_usd(month["revenue"]),
            f"{month['sale_count']} sales · avg {fmt_usd(month['average_sale'])}",
            trend=_trend(month["revenue"], last_month["revenue"], "the period before"),
        )
        self.card_profit.set(
            fmt_usd(month["gross_profit"]),
            f"{month['units']} units sold"
            + (f" · {month['refund_count']} refunded" if month["refund_count"] else ""),
            trend=_trend(month["gross_profit"], last_month["gross_profit"], ""),
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
            empty_message="Nothing needs reordering — every shelf is above its level.",
        )

        self.recent.set_rows(
            sales_service.list_sales(limit=12),
            tag_func=lambda row: "muted" if row["status"] != "Completed" else (),
            empty_message="No sales yet today. Start one from New Sale.",
        )
        self.header.set_subtitle(
            f"{dt.date.today():%A, %d %B %Y}   ·   1 USD = {D(rate):,.0f} LBP"
        )
