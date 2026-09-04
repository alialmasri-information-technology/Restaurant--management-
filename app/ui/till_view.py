"""The till: opening a shift, cash in and out, and closing with a count."""

from __future__ import annotations

import customtkinter as ctk

from app import config
from app.money import fmt_usd, parse_amount
from app.services import shifts as shifts_service
from app.ui import phrasing, receipt_actions, theme
from app.ui.shell import PageHeader
from app.ui.widgets import (
    Card,
    DataTable,
    FormModal,
    SectionTitle,
    StatCard,
    show_error,
    show_info,
)

MOVEMENT_COLUMNS = (
    ("at", "Time", 150, "w"),
    ("kind", "Direction", 90, "w"),
    ("amount_usd", "Amount", 110, "e"),
    ("reason", "Reason", 220, "w"),
    ("username", "By", 120, "w"),
)

SHIFT_COLUMNS = (
    ("shift_id", "#", 50, "w"),
    ("opened_at", "Opened", 140, "w"),
    ("closed_at", "Closed", 140, "w"),
    ("opened_by_name", "Opened by", 110, "w"),
    ("sales_total", "Sales", 100, "e"),
    ("expected_usd", "Expected", 100, "e"),
    ("counted_total", "Counted", 100, "e"),
    ("variance_usd", "Variance", 100, "e"),
    ("status", "Status", 90, "w"),
)


def _open_for(opened_at) -> str:
    """"Open for 4 hours" — but a shift opened seconds ago was just opened."""
    elapsed = phrasing.elapsed(opened_at)
    return "Just opened" if elapsed == "a moment" else f"Open for {elapsed}"


class TillView(ctk.CTkFrame):
    def __init__(self, parent, shell):
        super().__init__(parent, fg_color="transparent")
        self.shell = shell
        self.user = shell.user
        self.shift = None

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        self.header = PageHeader(self, "Till", "Cash control for this shift")
        self.header.grid(row=0, column=0, sticky="ew", padx=24, pady=(20, 12))
        self._build_actions()

        self.stats = ctk.CTkFrame(self, fg_color="transparent")
        self.stats.grid(row=1, column=0, sticky="ew", padx=24)
        for column in range(4):
            self.stats.grid_columnconfigure(column, weight=1, uniform="till")

        self.status_card = StatCard(self.stats, "Status", "-")
        self.status_card.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self.sales_card = StatCard(self.stats, "Sales this shift", "-")
        self.sales_card.grid(row=0, column=1, sticky="nsew", padx=8)
        self.cash_card = StatCard(self.stats, "Cash taken", "-")
        self.cash_card.grid(row=0, column=2, sticky="nsew", padx=8)
        self.expected_card = StatCard(
            self.stats, "Expected in drawer", "-", accent=theme.PRIMARY
        )
        self.expected_card.grid(row=0, column=3, sticky="nsew", padx=(8, 0))

        self._build_body()

    # ------------------------------------------------------------------ #

    def _build_actions(self) -> None:
        actions = self.header.actions
        self.open_button = ctk.CTkButton(
            actions, text="Open till", width=120, height=38,
            fg_color=theme.SUCCESS, hover_color=theme.SUCCESS_HOVER,
            command=self.open_shift,
        )
        self.open_button.grid(row=0, column=0, padx=(0, 8))

        self.in_button = ctk.CTkButton(
            actions, text="Cash in", width=110, height=38,
            fg_color=theme.NEUTRAL, hover_color=theme.NEUTRAL_HOVER,
            command=lambda: self.cash_movement(config.CASH_IN),
        )
        self.in_button.grid(row=0, column=1, padx=(0, 8))

        self.out_button = ctk.CTkButton(
            actions, text="Cash out", width=110, height=38,
            fg_color=theme.NEUTRAL, hover_color=theme.NEUTRAL_HOVER,
            command=lambda: self.cash_movement(config.CASH_OUT),
        )
        self.out_button.grid(row=0, column=2, padx=(0, 8))

        self.x_button = ctk.CTkButton(
            actions, text="X report", width=110, height=38,
            fg_color=theme.NEUTRAL, hover_color=theme.NEUTRAL_HOVER,
            command=lambda: self.print_report("X"),
        )
        self.x_button.grid(row=0, column=3, padx=(0, 8))

        # Not tied to a shift: it covers the whole day, including drawers that
        # have already been closed, so it stays available either way.
        ctk.CTkButton(
            actions, text="Day report", width=120, height=38,
            fg_color=theme.PRIMARY, hover_color=theme.PRIMARY_HOVER,
            command=self.print_day_report,
        ).grid(row=0, column=4, padx=(0, 8))

        self.close_button = ctk.CTkButton(
            actions, text="Close till", width=120, height=38,
            fg_color=theme.DANGER, hover_color=theme.DANGER_HOVER,
            command=self.close_shift,
        )
        self.close_button.grid(row=0, column=5)

    def _build_body(self) -> None:
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=3, column=0, sticky="nsew", padx=24, pady=(12, 16))
        body.grid_columnconfigure(0, weight=3, uniform="body")
        body.grid_columnconfigure(1, weight=2, uniform="body")
        # The summary card has a fixed number of lines, so the row it shares
        # with the movements table needs a floor or the card is clipped on a
        # short screen.
        body.grid_rowconfigure(1, weight=3, minsize=200)

        SectionTitle(body, "Cash movements").grid(row=0, column=0, sticky="w", pady=(0, 8))
        self.movements = DataTable(body, MOVEMENT_COLUMNS, id_key="movement_id", height=8)
        self.movements.set_formatter("amount_usd", lambda value, row: fmt_usd(value))
        self.movements.grid(row=1, column=0, sticky="nsew", padx=(0, 12))

        summary = Card(body)
        summary.grid(row=1, column=1, sticky="nsew")
        summary.grid_columnconfigure(1, weight=1)
        self.summary_rows: dict[str, ctk.CTkLabel] = {}

        ctk.CTkLabel(
            summary, text="Through the drawer", font=theme.font(15, "bold"),
            text_color=theme.TEXT, anchor="w",
        ).grid(row=0, column=0, columnspan=2, sticky="ew", padx=16, pady=(12, 8))

        # The float and the expected total already have stat cards above, so
        # this card only carries what moved during the shift.
        labels = (
            ("cash_sales", "Cash sales"),
            ("non_cash_sales", "Card and other"),
            ("cash_in", "Paid in"),
            ("cash_out", "Paid out"),
            ("cash_returns", "Cash refunds"),
        )
        for index, (key, label) in enumerate(labels, start=1):
            bold = False
            ctk.CTkLabel(
                summary, text=label, font=theme.font(12, "bold" if bold else "normal"),
                text_color=theme.TEXT if bold else theme.TEXT_MUTED, anchor="w",
            ).grid(row=index, column=0, sticky="w", padx=(16, 8), pady=2)
            value = ctk.CTkLabel(
                summary, text="-", font=theme.font(13, "bold" if bold else "normal"),
                text_color=theme.TEXT, anchor="e",
            )
            value.grid(row=index, column=1, sticky="e", padx=(8, 16), pady=2)
            self.summary_rows[key] = value

        SectionTitle(body, "Recent shifts").grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(12, 6)
        )
        self.history = DataTable(body, SHIFT_COLUMNS, id_key="shift_id", height=5)
        for key in ("sales_total", "expected_usd", "counted_total", "variance_usd"):
            self.history.set_formatter(
                key, lambda value, row: fmt_usd(value) if value is not None else "-"
            )
        self.history.set_formatter(
            "opened_at", lambda value, _row: phrasing.relative_time(value)
        )
        self.history.set_formatter(
            "closed_at", lambda value, _row: phrasing.relative_time(value, empty="Still open")
        )
        self.history.grid(row=3, column=0, columnspan=2, sticky="nsew")
        self.history.on_double_click(self.print_selected_report)
        body.grid_rowconfigure(3, weight=2, minsize=130)

    # ------------------------------------------------------------------ #

    def refresh(self) -> None:
        self.shift = shifts_service.current_shift()
        is_open = self.shift is not None

        self.open_button.configure(state="disabled" if is_open else "normal")
        for button in (self.in_button, self.out_button, self.x_button, self.close_button):
            button.configure(state="normal" if is_open else "disabled")

        if not is_open:
            self.status_card.set("Closed", "Open it before the first sale")
            for card in (self.sales_card, self.cash_card, self.expected_card):
                card.set("-", "")
            for label in self.summary_rows.values():
                label.configure(text="-")
            self.movements.set_rows(
                [], empty_message="Open the till to start a shift."
            )
            self.header.set_subtitle("The till is closed")
        else:
            totals = shifts_service.totals(self.shift["shift_id"])
            self.status_card.set(
                f"Open #{self.shift['shift_id']}",
                _open_for(self.shift["opened_at"]),
            )
            self.sales_card.set(
                fmt_usd(totals["sales_total"]), phrasing.plural(totals["sale_count"], "sale")
            )
            self.cash_card.set(
                fmt_usd(totals["cash_sales"]),
                f"{fmt_usd(totals['non_cash_sales'])} card and other",
            )
            self.expected_card.set(
                fmt_usd(totals["expected_usd"]),
                f"float {fmt_usd(totals['opening_float'])}",
            )
            for key, label in self.summary_rows.items():
                label.configure(text=fmt_usd(totals[key]))
            self.movements.set_rows(
                shifts_service.movements(self.shift["shift_id"]),
                empty_message="Nothing has been paid in or out of the drawer yet.",
            )
            self.header.set_subtitle(
                f"Opened by {self.shift['opened_by_name'] or 'somebody'}, "
                f"{phrasing.relative_time(self.shift['opened_at']).lower()}"
            )

        self.history.set_rows(
            [self._history_row(row) for row in shifts_service.list_shifts(limit=25)],
            tag_func=self._history_tag,
            empty_message="No shifts yet. The first one starts when you open the till.",
        )

    @staticmethod
    def _history_row(row) -> dict:
        data = dict(row)
        rate = data.get("exchange_rate") or 0
        counted = data.get("counted_usd")
        if counted is not None and data.get("counted_lbp") and rate:
            counted = counted + (data["counted_lbp"] / rate)
        data["counted_total"] = counted
        if not data.get("closed_at"):
            data["counted_total"] = None
            data["variance_usd"] = None
        data["closed_at"] = data.get("closed_at") or ""
        return data

    @staticmethod
    def _history_tag(row):
        if row["status"] == config.SHIFT_OPEN:
            return "success"
        variance = row.get("variance_usd") or 0
        if abs(variance) >= 1:
            return "danger"
        if variance:
            return "warning"
        return ()

    # ------------------------------------------------------------------ #

    def open_shift(self) -> None:
        def submit(values):
            amount = parse_amount(values["opening_float"], "opening float")
            shifts_service.open_shift(
                self.user.user_id, opening_float=amount, note=values["note"]
            )

        modal = FormModal(
            self, "Open the till",
            fields=[
                {
                    "key": "opening_float", "label": "Opening float (USD)", "value": "0.00",
                    "hint": "Count the cash already in the drawer before selling.",
                },
                {"key": "note", "label": "Note (optional)", "type": "text"},
            ],
            on_submit=submit, submit_text="Open till",
        )
        if modal.wait_result() is not None:
            self.refresh()
            self.shell.refresh_current()

    def cash_movement(self, kind: str) -> None:
        if self.shift is None:
            return
        reasons = (
            config.CASH_REASONS_IN if kind == config.CASH_IN else config.CASH_REASONS_OUT
        )

        def submit(values):
            amount = parse_amount(values["amount"], "amount")
            reason = values["reason"]
            if values["detail"].strip():
                reason = f"{reason} - {values['detail'].strip()}"
            shifts_service.add_cash_movement(
                self.shift["shift_id"], self.user.user_id, kind, amount, reason
            )

        title = "Cash into the drawer" if kind == config.CASH_IN else "Cash out of the drawer"
        modal = FormModal(
            self, title,
            fields=[
                {"key": "amount", "label": "Amount (USD)", "value": ""},
                {"key": "reason", "label": "Reason", "type": "option", "values": list(reasons)},
                {"key": "detail", "label": "Detail (optional)"},
            ],
            on_submit=submit, submit_text="Record",
        )
        if modal.wait_result() is not None:
            self.refresh()

    def close_shift(self) -> None:
        if self.shift is None:
            return
        totals = shifts_service.totals(self.shift["shift_id"])
        shift_id = self.shift["shift_id"]

        def submit(values):
            summary = shifts_service.close_shift(
                shift_id, self.user.user_id,
                counted_usd=parse_amount(values["counted_usd"], "counted USD"),
                counted_lbp=parse_amount(values["counted_lbp"], "counted LBP"),
                note=values["note"],
            )
            self._closed_summary = summary

        self._closed_summary = None
        modal = FormModal(
            self, "Close the till",
            fields=[
                {
                    "key": "expected", "label": "Expected in drawer", "type": "readonly",
                    "value": fmt_usd(totals["expected_usd"]),
                },
                {"key": "counted_usd", "label": "Counted USD", "value": "0.00"},
                {
                    "key": "counted_lbp", "label": "Counted LBP", "value": "0",
                    "hint": "Converted at the rate this shift opened with.",
                },
                {"key": "note", "label": "Note (optional)", "type": "text"},
            ],
            on_submit=submit, submit_text="Close till",
        )
        if modal.wait_result() is None:
            return

        summary = self._closed_summary or {}
        variance = summary.get("variance_usd", 0)
        if variance > 0:
            verdict = f"The drawer is over by {fmt_usd(variance)}."
        elif variance < 0:
            verdict = f"The drawer is short by {fmt_usd(abs(variance))}."
        else:
            verdict = "The drawer balances exactly."

        self.refresh()
        self.shell.refresh_current()
        show_info(
            self,
            f"Till closed.\n\nExpected {fmt_usd(summary.get('expected_usd', 0))}\n"
            f"Counted {fmt_usd(summary.get('counted_total_usd', 0))}\n\n{verdict}",
            "Till closed",
        )
        receipt_actions.print_shift_report(self, shift_id, kind="Z")

    def print_day_report(self) -> None:
        receipt_actions.print_day_report(self)

    def print_report(self, kind: str = "X") -> None:
        if self.shift is None:
            return
        receipt_actions.print_shift_report(self, self.shift["shift_id"], kind=kind)

    def print_selected_report(self) -> None:
        shift_id = self.history.selected_int()
        if shift_id is None:
            show_error(self, "Select a shift first.", "Nothing selected")
            return
        shift = shifts_service.get_shift(shift_id)
        kind = "X" if shift and shift["status"] == config.SHIFT_OPEN else "Z"
        receipt_actions.print_shift_report(self, shift_id, kind=kind)
