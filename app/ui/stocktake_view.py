"""Stock take: count the shelves, review the variance, post it.

The layout follows how a count actually runs. The scan box has focus and stays
focused, because the person holding the scanner cannot also be clicking rows;
everything else on the screen is there to be glanced at, not driven.
"""

from __future__ import annotations

import customtkinter as ctk

from app import config
from app.money import fmt_usd
from app.services import products as products_service
from app.services import stocktake as stocktake_service
from app.ui import theme
from app.ui.shell import PageHeader
from app.ui.widgets import (
    Card,
    DataTable,
    FormModal,
    SectionTitle,
    StatCard,
    ask_confirm,
    show_error,
    show_info,
)

FILTERS = ("Everything", "Not counted yet", "Variances only")
ALL_CATEGORIES = "All categories"


class StockTakeView(ctk.CTkFrame):
    def __init__(self, parent, shell):
        super().__init__(parent, fg_color="transparent")
        self.shell = shell
        self.count = None

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        self.header = PageHeader(self, "Stock take", "")
        self.header.grid(row=0, column=0, sticky="ew", padx=24, pady=(20, 14))

        self.start_button = ctk.CTkButton(
            self.header.actions, text="Start a count", height=38, width=150,
            font=theme.font(13, "bold"), command=self._start,
        )
        self.start_button.grid(row=0, column=0, padx=(0, 8))
        self.history_button = ctk.CTkButton(
            self.header.actions, text="Past counts", height=38, width=130,
            fg_color=theme.NEUTRAL, hover_color=theme.NEUTRAL_HOVER,
            command=self._show_history,
        )
        self.history_button.grid(row=0, column=1)

        self._build_stats()
        self._build_toolbar()
        self._build_table()

    # ------------------------------------------------------------------ #
    # Chrome
    # ------------------------------------------------------------------ #

    def _build_stats(self) -> None:
        stats = ctk.CTkFrame(self, fg_color="transparent")
        stats.grid(row=1, column=0, sticky="ew", padx=24)
        for column in range(4):
            stats.grid_columnconfigure(column, weight=1, uniform="stat")

        self.card_progress = StatCard(stats, "Counted", accent=theme.PRIMARY)
        self.card_short = StatCard(stats, "Short on the shelf", accent=theme.DANGER)
        self.card_over = StatCard(stats, "More than expected", accent=theme.SUCCESS)
        self.card_value = StatCard(stats, "Variance at cost")
        for column, card in enumerate(
            (self.card_progress, self.card_short, self.card_over, self.card_value)
        ):
            card.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else 12, 0))

    def _build_toolbar(self) -> None:
        bar = Card(self)
        bar.grid(row=2, column=0, sticky="ew", padx=24, pady=(16, 0))
        bar.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            bar, text="Scan or type a code", font=theme.font(12),
            text_color=theme.TEXT_MUTED, anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=(16, 8), pady=(14, 0))

        self.scan_var = ctk.StringVar()
        self.scan_entry = ctk.CTkEntry(
            bar, textvariable=self.scan_var, height=40, font=theme.font(15),
            placeholder_text="Barcode or SKU — Enter counts one unit",
        )
        self.scan_entry.grid(row=1, column=0, columnspan=2, sticky="ew", padx=16, pady=(4, 6))
        self.scan_entry.bind("<Return>", lambda _event: self._scan())

        self.scan_feedback = ctk.CTkLabel(
            bar, text="", font=theme.font(12), text_color=theme.TEXT_MUTED, anchor="w"
        )
        self.scan_feedback.grid(row=2, column=0, columnspan=2, sticky="ew", padx=16)

        controls = ctk.CTkFrame(bar, fg_color="transparent")
        controls.grid(row=3, column=0, columnspan=2, sticky="ew", padx=16, pady=(8, 14))
        controls.grid_columnconfigure(2, weight=1)

        self.search_var = ctk.StringVar()
        search = ctk.CTkEntry(
            controls, textvariable=self.search_var, width=240, height=34,
            placeholder_text="Filter the sheet by name or SKU",
        )
        search.grid(row=0, column=0, padx=(0, 8))
        search.bind("<KeyRelease>", lambda _event: self._render_lines())

        self.filter_var = ctk.StringVar(value=FILTERS[0])
        ctk.CTkOptionMenu(
            controls, variable=self.filter_var, values=list(FILTERS),
            width=170, height=34, command=lambda _value: self._render_lines(),
        ).grid(row=0, column=1, padx=(0, 8))

        hovers = {
            theme.SUCCESS: theme.SUCCESS_HOVER,
            theme.DANGER: theme.DANGER_HOVER,
            theme.PRIMARY: theme.PRIMARY_HOVER,
            theme.NEUTRAL: theme.NEUTRAL_HOVER,
        }
        self._action_buttons = []
        for column, (label, command, colour) in enumerate((
            ("Enter a count", self._enter_count, theme.PRIMARY),
            ("Clear the line", self._clear_line, theme.NEUTRAL),
            ("Accept the rest", self._accept_rest, theme.NEUTRAL),
            ("Apply", self._apply, theme.SUCCESS),
            ("Cancel count", self._cancel, theme.DANGER),
        ), start=3):
            button = ctk.CTkButton(
                controls, text=label, height=34, width=130, command=command,
                fg_color=colour, hover_color=hovers[colour],
            )
            button.grid(row=0, column=column, padx=(0, 8))
            self._action_buttons.append(button)

    def _build_table(self) -> None:
        self.table = DataTable(
            self,
            columns=[
                ("sku_at_count", "SKU", 120, "w"),
                ("name_at_count", "Product", 260, "w"),
                ("expected_qty", "System", 90, "center"),
                ("counted_qty", "Counted", 90, "center"),
                ("variance_qty", "Variance", 90, "center"),
                ("variance_usd", "At cost", 110, "e"),
            ],
            id_key="product_id",
            height=14,
        )
        self.table.set_formatter(
            "counted_qty", lambda value, _row: "—" if value is None else str(value)
        )
        self.table.set_formatter(
            "variance_qty",
            lambda value, row: "—" if row["counted_qty"] is None else f"{value:+d}",
        )
        self.table.set_formatter(
            "variance_usd",
            lambda value, row: "" if row["counted_qty"] is None else fmt_usd(value),
        )
        self.table.grid(row=3, column=0, sticky="nsew", padx=24, pady=(16, 24))
        self.table.on_double_click(self._enter_count)
        self.table.on_return(self._enter_count)

    # ------------------------------------------------------------------ #
    # State
    # ------------------------------------------------------------------ #

    def refresh(self) -> None:
        self.count = stocktake_service.current_count()
        open_now = self.count is not None

        self.start_button.configure(state="normal" if not open_now else "disabled")
        for button in self._action_buttons:
            button.configure(state="normal" if open_now else "disabled")
        self.scan_entry.configure(state="normal" if open_now else "disabled")

        if not open_now:
            self.header.set_subtitle(
                "No count is open. Start one to freeze a worksheet of what the "
                "system thinks is on the shelves."
            )
            for card in (
                self.card_progress, self.card_short, self.card_over, self.card_value
            ):
                card.set("—", "")
            self.table.set_rows(
                [], empty_message="Start a count to build the sheet."
            )
            return

        self.header.set_subtitle(
            f"{self.count['reference']}   ·   {self.count['scope']}   ·   opened "
            f"{self.count['opened_at']} by {self.count['opened_by_name'] or 'unknown'}"
        )
        self._render_lines()
        self._render_stats()
        self.scan_entry.focus_set()

    def _render_stats(self) -> None:
        totals = stocktake_service.summary(self.count["stock_take_id"])
        counted = totals.get("counted_lines", 0)
        lines = totals.get("line_count", 0) or 1
        self.card_progress.set(
            f"{counted} / {totals.get('line_count', 0)}",
            f"{totals.get('uncounted_lines', 0)} still to count "
            f"· {counted * 100 // lines}% done",
        )
        self.card_short.set(
            f"{totals.get('shortage_units', 0)} units",
            f"across {totals.get('variance_lines', 0)} line(s) with a variance",
        )
        self.card_over.set(f"{totals.get('surplus_units', 0)} units", "found on the shelf")
        net_value = totals.get("net_value", 0) or 0
        self.card_value.set(
            fmt_usd(net_value),
            "written off" if net_value < 0 else "added back" if net_value else "in balance",
        )

    def _render_lines(self) -> None:
        if self.count is None:
            return
        choice = self.filter_var.get()
        rows = stocktake_service.list_items(
            self.count["stock_take_id"],
            search=self.search_var.get(),
            only_uncounted=(choice == FILTERS[1]),
            only_variances=(choice == FILTERS[2]),
        )
        self.table.set_rows(
            rows,
            tag_func=_line_tag,
            empty_message="Nothing on the sheet matches that filter.",
        )

    # ------------------------------------------------------------------ #
    # Actions
    # ------------------------------------------------------------------ #

    def _start(self) -> None:
        categories = products_service.list_categories()
        names = [ALL_CATEGORIES] + [row["name"] for row in categories]
        fields = [
            {"key": "category", "label": "What to count", "type": "option",
             "values": names,
             "hint": "A full count is the honest one. Counting a single category "
                     "is a quicker spot check."},
            {"key": "include_zero", "label": "Include products the system says are "
                                             "out of stock", "type": "check",
             "value": True,
             "hint": "Worth leaving on — a forgotten box is exactly the kind of "
                     "thing a count is for."},
            {"key": "note", "label": "Note", "type": "entry",
             "placeholder": "End of month, after the delivery, …"},
        ]

        def submit(values):
            category_id = None
            if values["category"] != ALL_CATEGORIES:
                category_id = next(
                    (row["category_id"] for row in categories
                     if row["name"] == values["category"]),
                    None,
                )
            stocktake_service.open_count(
                self.shell.user.user_id,
                category_id=category_id,
                include_zero_stock=bool(values["include_zero"]),
                note=values["note"],
            )

        if FormModal(self, "Start a stock take", fields, submit,
                     submit_text="Start counting").wait_result():
            self.refresh()

    def _scan(self) -> None:
        code = self.scan_var.get().strip()
        if not code or self.count is None:
            return
        try:
            product = stocktake_service.scan(self.count["stock_take_id"], code)
        except stocktake_service.StockTakeError as exc:
            self.scan_feedback.configure(text=str(exc), text_color=theme.DANGER)
            self.scan_var.set("")
            return

        line = next(
            (row for row in stocktake_service.list_items(self.count["stock_take_id"])
             if row["product_id"] == product["product_id"]),
            None,
        )
        counted = line["counted_qty"] if line else 1
        self.scan_feedback.configure(
            text=f"{product['name']} — counted {counted}", text_color=theme.SUCCESS
        )
        self.scan_var.set("")
        self._render_lines()
        self._render_stats()
        self.scan_entry.focus_set()

    def _selected_line(self):
        product_id = self.table.selected_int()
        if product_id is None or self.count is None:
            show_error(self, "Pick a line on the sheet first.", "Nothing selected")
            return None
        return next(
            (row for row in stocktake_service.list_items(self.count["stock_take_id"])
             if row["product_id"] == product_id),
            None,
        )

    def _enter_count(self) -> None:
        line = self._selected_line()
        if line is None:
            return
        fields = [
            {"key": "product", "label": "Product", "type": "readonly",
             "value": f"{line['sku_at_count']} — {line['name_at_count']}"},
            {"key": "expected", "label": "The system says", "type": "readonly",
             "value": str(line["expected_qty"])},
            {"key": "counted", "label": "Counted on the shelf", "type": "number",
             "value": "" if line["counted_qty"] is None else str(line["counted_qty"]),
             "hint": "Type what is actually there. Nothing moves until the count "
                     "is applied."},
        ]

        def submit(values):
            stocktake_service.record_count(
                self.count["stock_take_id"], line["product_id"], values["counted"]
            )

        if FormModal(self, "Record a count", fields, submit,
                     submit_text="Record").wait_result():
            self._render_lines()
            self._render_stats()

    def _clear_line(self) -> None:
        line = self._selected_line()
        if line is None:
            return
        stocktake_service.clear_line(self.count["stock_take_id"], line["product_id"])
        self._render_lines()
        self._render_stats()

    def _accept_rest(self) -> None:
        totals = stocktake_service.summary(self.count["stock_take_id"])
        remaining = totals.get("uncounted_lines", 0)
        if not remaining:
            show_info(self, "Every line has been counted.", "Nothing to accept")
            return
        if not ask_confirm(
            self,
            f"Record the system figure for the {remaining} line(s) nobody has "
            "counted?\n\nThey will be treated as agreeing, not as empty shelves.",
            "Accept the rest",
        ):
            return
        stocktake_service.count_remaining_as_expected(self.count["stock_take_id"])
        self._render_lines()
        self._render_stats()

    def _apply(self) -> None:
        if not self.shell.user.is_admin:
            show_error(
                self,
                "Only an administrator can post a stock take. The count itself is "
                "saved — ask them to review and apply it.",
                "Not permitted",
            )
            return

        totals = stocktake_service.summary(self.count["stock_take_id"])
        net_value = totals.get("net_value", 0) or 0
        message = (
            f"Apply {self.count['reference']}?\n\n"
            f"{totals.get('variance_lines', 0)} line(s) differ: "
            f"{totals.get('shortage_units', 0)} unit(s) short, "
            f"{totals.get('surplus_units', 0)} over — "
            f"{fmt_usd(net_value)} at cost.\n\n"
        )
        if totals.get("uncounted_lines"):
            message += (
                f"{totals['uncounted_lines']} line(s) were never counted and will "
                "be left exactly as they are.\n\n"
            )
        message += "This writes the difference to stock and closes the count."
        if not ask_confirm(self, message, "Apply the stock take"):
            return

        try:
            result = stocktake_service.apply_count(
                self.count["stock_take_id"], self.shell.user.user_id
            )
        except stocktake_service.StockTakeError as exc:
            show_error(self, exc, "Could not apply the count")
            return

        note = f"{result['adjustments']} product(s) adjusted."
        if result["clamped"]:
            note += (
                "\n\nStock for these was held at zero, because more was sold during "
                "the count than was found on the shelf:\n"
                + ", ".join(result["clamped"])
            )
        show_info(self, note, "Stock take applied")
        self.shell.invalidate("dashboard", "products", "pos", "reports")
        self.refresh()

    def _cancel(self) -> None:
        if not ask_confirm(
            self,
            f"Abandon {self.count['reference']}?\n\nStock is left untouched and the "
            "counted figures are kept for the record, but nothing is posted.",
            "Cancel the stock take",
        ):
            return
        stocktake_service.cancel_count(self.count["stock_take_id"], self.shell.user.user_id)
        self.refresh()

    def _show_history(self) -> None:
        HistoryModal(self)


def _line_tag(row) -> str:
    if row["counted_qty"] is None:
        return "muted"
    variance = row["counted_qty"] - row["expected_qty"]
    if variance < 0:
        return "danger"
    if variance > 0:
        return "success"
    return ""


class HistoryModal(ctk.CTkToplevel):
    """Past counts, so a run of shrinkage in one aisle is visible over time."""

    def __init__(self, parent):
        super().__init__(parent)
        self.title("Past stock takes")
        self.configure(fg_color=theme.BG)
        self.geometry("880x480")
        self.transient(parent.winfo_toplevel())

        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        SectionTitle(self, "Past stock takes").grid(
            row=0, column=0, sticky="ew", padx=20, pady=(18, 10)
        )

        table = DataTable(
            self,
            columns=[
                ("reference", "Reference", 140, "w"),
                ("opened_at", "Opened", 150, "w"),
                ("scope", "Scope", 160, "w"),
                ("opened_by_name", "By", 100, "w"),
                ("progress", "Counted", 100, "center"),
                ("status", "Status", 100, "center"),
                ("variance", "Variance at cost", 130, "e"),
            ],
            id_key="stock_take_id",
        )
        rows = []
        for row in stocktake_service.list_counts():
            entry = dict(row)
            entry["progress"] = f"{row['counted_count']}/{row['line_count']}"
            entry["variance"] = fmt_usd(
                stocktake_service.variance_value(row["stock_take_id"])
            )
            rows.append(entry)
        table.set_rows(
            rows,
            tag_func=lambda row: (
                "muted" if row["status"] == config.TAKE_CANCELLED
                else "warning" if row["status"] == config.TAKE_OPEN
                else ""
            ),
            empty_message="No stock takes have been run yet.",
        )
        table.grid(row=1, column=0, sticky="nsew", padx=20, pady=(0, 12))

        ctk.CTkButton(
            self, text="Close", width=120, height=36,
            fg_color=theme.NEUTRAL, hover_color=theme.NEUTRAL_HOVER,
            command=self.destroy,
        ).grid(row=2, column=0, sticky="e", padx=20, pady=(0, 18))

        self.bind("<Escape>", lambda _event: self.destroy())
        self.after(80, self._grab)

    def _grab(self) -> None:
        if not self.winfo_exists():
            return
        try:
            self.grab_set()
            self.focus_force()
        except Exception:  # noqa: BLE001  # pragma: no cover - window gone
            pass
