"""The till: opening a shift, cash in and out, and closing with a count."""

from __future__ import annotations

import customtkinter as ctk

from app import config
from app.money import fmt_usd, parse_amount
from app.services import giftcards as giftcards_service
from app.services import layaways as layaways_service
from app.services import shifts as shifts_service
from app.ui import phrasing, receipt_actions, theme
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
from app.ui.widgets import debounce as widgets_debounce

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

        ctk.CTkButton(
            actions, text="Gift cards", width=110, height=38,
            fg_color=theme.NEUTRAL, hover_color=theme.NEUTRAL_HOVER,
            command=lambda: GiftCardsModal(self, self.user),
        ).grid(row=0, column=6, padx=(8, 0))

        ctk.CTkButton(
            actions, text="Layaways", width=110, height=38,
            fg_color=theme.NEUTRAL, hover_color=theme.NEUTRAL_HOVER,
            command=lambda: LayawaysModal(self, self.user),
        ).grid(row=0, column=7, padx=(8, 0))

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


class GiftCardsModal(ctk.CTkToplevel):
    """What the shop owes on cards, and which one paid for what."""

    def __init__(self, parent, user):
        super().__init__(parent)
        self.title("Gift cards")
        self.configure(fg_color=theme.BG)
        self.geometry("880x520")
        self.transient(parent.winfo_toplevel())
        self.user = user

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        SectionTitle(self, "Gift cards").grid(
            row=0, column=0, sticky="ew", padx=18, pady=(18, 4)
        )
        self.summary_label = ctk.CTkLabel(
            self, text="", font=theme.font(11), text_color=theme.TEXT_MUTED, anchor="w"
        )
        self.summary_label.grid(row=1, column=0, sticky="ew", padx=18)

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=2, column=0, sticky="ew", padx=18, pady=(8, 4))
        bar.grid_columnconfigure(0, weight=1)
        self.search_var = ctk.StringVar()
        self.search_var.trace_add("write", widgets_debounce(self, 250, self.reload))
        ctk.CTkEntry(
            bar, textvariable=self.search_var, height=34,
            placeholder_text="Search by code or note…",
        ).grid(row=0, column=0, sticky="ew", padx=(0, 8))

        self.table = DataTable(
            self,
            columns=[
                ("code", "Code", 170, "w"),
                ("balance_usd", "Balance", 110, "e"),
                ("initial_usd", "Loaded", 110, "e"),
                ("status", "Status", 90, "center"),
                ("sold_usd", "Sold for", 110, "e"),
                ("event_count", "Movements", 90, "center"),
                ("note", "Note", 180, "w"),
            ],
            id_key="card_id",
            height=12,
        )
        self.table.grid(row=3, column=0, sticky="nsew", padx=18, pady=(4, 6))
        for column in ("balance_usd", "initial_usd", "sold_usd"):
            self.table.set_formatter(column, lambda value, _row: fmt_usd(value))
        self.table.set_formatter(
            "status", lambda value, row: "" if row["status"] == "Active" else value
        )
        self.table.on_double_click(self._show_history)

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=4, column=0, sticky="ew", padx=18, pady=(0, 16))
        footer.grid_columnconfigure(0, weight=1)
        ctk.CTkButton(
            footer, text="History", width=110, height=36,
            fg_color=theme.NEUTRAL, hover_color=theme.NEUTRAL_HOVER,
            command=self._show_history,
        ).grid(row=0, column=0, sticky="w")
        if self.user.is_admin:
            ctk.CTkButton(
                footer, text="Issue a card", width=130, height=36,
                fg_color=theme.PRIMARY, hover_color=theme.PRIMARY_HOVER,
                command=self._issue,
            ).grid(row=0, column=1, padx=(0, 8))
            self.status_button = ctk.CTkButton(
                footer, text="Disable", width=110, height=36,
                fg_color=theme.DANGER, hover_color=theme.DANGER_HOVER,
                command=self._toggle,
            )
            self.status_button.grid(row=0, column=2)
        ctk.CTkButton(
            footer, text="Close", width=110, height=36,
            fg_color=theme.NEUTRAL, hover_color=theme.NEUTRAL_HOVER,
            command=self.destroy,
        ).grid(row=0, column=3, padx=(8, 0))

        self.bind("<Escape>", lambda _event: self.destroy())
        self.after(80, self._grab)
        self.reload()

    def _grab(self) -> None:
        if not self.winfo_exists():
            return
        try:
            self.grab_set()
            self.focus_force()
        except Exception:  # noqa: BLE001  # pragma: no cover - window gone
            pass

    def reload(self) -> None:
        self.table.set_rows(
            giftcards_service.list_cards(self.search_var.get()),
            empty_message="No gift cards yet. Sell one at the till, or issue one here.",
        )
        totals = giftcards_service.totals()
        self.summary_label.configure(
            text=(
                f"{totals['cards']} card(s) · {totals['active']} active · "
                f"the shop owes {fmt_usd(totals['liability_usd'])} on cards"
            )
        )
        self._refresh_status_button()

    def _refresh_status_button(self) -> None:
        if not self.user.is_admin:
            return
        card = self._selected()
        disabled = card is not None and card["status"] == giftcards_service.DISABLED
        self.status_button.configure(
            text="Enable" if disabled else "Disable",
            fg_color=theme.SUCCESS if disabled else theme.DANGER,
            hover_color=theme.SUCCESS_HOVER if disabled else theme.DANGER_HOVER,
        )

    def _selected(self):
        card_id = self.table.selected_int()
        return giftcards_service.get_by_id(card_id) if card_id is not None else None

    def _show_history(self) -> None:
        card = self._selected()
        if card is None:
            show_error(self, "Pick a card first.", "Nothing selected")
            return
        GiftCardHistoryModal(self, card)

    def _issue(self) -> None:
        fields = [
            {"key": "amount", "label": "How much to load (USD)", "type": "number"},
            {"key": "note", "label": "Who it is for (optional)", "type": "entry"},
        ]

        def submit(values):
            card = giftcards_service.issue(
                values["amount"], user_id=self.user.user_id, note=values["note"]
            )
            show_info(self, f"Card {card['code']} issued, loaded with "
                           f"{fmt_usd(card['balance_usd'])}.", "Card issued")

        if FormModal(self, "Issue a gift card", fields, submit,
                     submit_text="Issue").wait_result():
            self.reload()

    def _toggle(self) -> None:
        card = self._selected()
        if card is None:
            show_error(self, "Pick a card first.", "Nothing selected")
            return
        disabled = card["status"] == giftcards_service.DISABLED
        if not ask_confirm(
            self,
            (f"Re-enable {card['code']}?" if disabled
             else f"Disable {card['code']}?\n\nIt will not be accepted at the "
                  "till while it is disabled. Its balance is unchanged."),
            "Gift card",
        ):
            return
        giftcards_service.set_status(
            card["card_id"],
            giftcards_service.ACTIVE if disabled else giftcards_service.DISABLED,
        )
        self.reload()


class GiftCardHistoryModal(ctk.CTkToplevel):
    """One card's movements, newest first."""

    def __init__(self, parent, card):
        super().__init__(parent)
        self.title(f"Gift card {card['code']}")
        self.configure(fg_color=theme.BG)
        self.geometry("620x420")
        self.transient(parent.winfo_toplevel())

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        SectionTitle(
            self,
            f"{card['code']}  ·  {fmt_usd(card['balance_usd'])} left of "
            f"{fmt_usd(card['initial_usd'])}",
        ).grid(row=0, column=0, sticky="ew", padx=18, pady=(18, 8))

        table = DataTable(
            self,
            columns=[
                ("at", "When", 150, "w"),
                ("kind", "Kind", 90, "center"),
                ("amount_usd", "Amount", 110, "e"),
                ("username", "By", 110, "w"),
                ("note", "Note", 180, "w"),
            ],
            id_key="event_id",
            height=10,
        )
        table.grid(row=1, column=0, sticky="nsew", padx=18, pady=(0, 10))
        table.set_formatter(
            "amount_usd", lambda value, _row: f"+{fmt_usd(value)}" if value >= 0 else fmt_usd(value)
        )
        table.set_rows(
            giftcards_service.history(card["card_id"]),
            empty_message="No movements yet.",
        )

        ctk.CTkButton(
            self, text="Close", width=110, height=36,
            fg_color=theme.NEUTRAL, hover_color=theme.NEUTRAL_HOVER,
            command=self.destroy,
        ).grid(row=2, column=1, sticky="e", padx=18, pady=(0, 16))
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


class LayawaysModal(ctk.CTkToplevel):
    """Goods set aside and the money still owed on them."""

    def __init__(self, parent, user):
        super().__init__(parent)
        self.title("Layaways")
        self.configure(fg_color=theme.BG)
        self.geometry("920x540")
        self.transient(parent.winfo_toplevel())
        self.user = user

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        SectionTitle(self, "Layaways").grid(
            row=0, column=0, sticky="ew", padx=18, pady=(18, 4)
        )
        self.summary_label = ctk.CTkLabel(
            self, text="", font=theme.font(11), text_color=theme.TEXT_MUTED, anchor="w"
        )
        self.summary_label.grid(row=1, column=0, sticky="ew", padx=18)

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=2, column=0, sticky="ew", padx=18, pady=(8, 4))
        bar.grid_columnconfigure(0, weight=1)
        self.search_var = ctk.StringVar()
        self.search_var.trace_add("write", widgets_debounce(self, 250, self.reload))
        ctk.CTkEntry(
            bar, textvariable=self.search_var, height=34,
            placeholder_text="Search by reference, customer or note…",
        ).grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.show_all = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            bar, text="Show collected and cancelled", variable=self.show_all,
            command=self.reload,
        ).grid(row=0, column=1)

        self.table = DataTable(
            self,
            columns=[
                ("reference", "Reference", 150, "w"),
                ("customer_name", "Customer", 150, "w"),
                ("item_count", "Items", 60, "center"),
                ("total_usd", "Total", 100, "e"),
                ("deposit_usd", "Deposited", 100, "e"),
                ("owing", "Still owed", 100, "e"),
                ("due_date", "Due", 100, "w"),
                ("status", "Status", 90, "center"),
            ],
            id_key="layaway_id",
            height=12,
        )
        self.table.grid(row=3, column=0, sticky="nsew", padx=18, pady=(4, 6))
        for column in ("total_usd", "deposit_usd"):
            self.table.set_formatter(column, lambda value, _row: fmt_usd(value))
        self.table.set_formatter("status", lambda value, row: "" if value == "Held" else value)
        self.table.set_formatter(
            "owing", lambda value, row: (
                "" if row["status"] != layaways_service.HELD else fmt_usd(value)
            )
        )
        self.table.set_formatter(
            "due_date", lambda value, row: value or "—"
        )

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=4, column=0, sticky="ew", padx=18, pady=(0, 16))
        footer.grid_columnconfigure(0, weight=1)
        ctk.CTkButton(
            footer, text="Collect", width=120, height=36,
            fg_color=theme.SUCCESS, hover_color=theme.SUCCESS_HOVER,
            command=self._collect,
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(
            footer, text="Cancel layaway", width=140, height=36,
            fg_color=theme.DANGER, hover_color=theme.DANGER_HOVER,
            command=self._cancel,
        ).grid(row=0, column=1, padx=(8, 0))
        ctk.CTkButton(
            footer, text="Close", width=110, height=36,
            fg_color=theme.NEUTRAL, hover_color=theme.NEUTRAL_HOVER,
            command=self.destroy,
        ).grid(row=0, column=2, padx=(8, 0))

        self.bind("<Escape>", lambda _event: self.destroy())
        self.after(80, self._grab)
        self.reload()

    def _grab(self) -> None:
        if not self.winfo_exists():
            return
        try:
            self.grab_set()
            self.focus_force()
        except Exception:  # noqa: BLE001  # pragma: no cover - window gone
            pass

    def reload(self) -> None:
        rows = [
            {**dict(row), "owing": round(row["total_usd"] - row["deposit_usd"], 2)}
            for row in layaways_service.list_layaways(
                None if self.show_all.get() else layaways_service.HELD,
                search=self.search_var.get(),
            )
        ]
        self.table.set_rows(
            rows,
            empty_message="Nothing is set aside. Hold a cart on the New Sale screen.",
        )
        totals = layaways_service.summary()
        overdue = f" · {totals['overdue']} past their date" if totals["overdue"] else ""
        self.summary_label.configure(
            text=(
                f"{totals['held']} held · {fmt_usd(totals['value_usd'])} still to come in"
                + overdue
            )
        )

    def _selected(self):
        layaway_id = self.table.selected_int()
        return layaways_service.get_layaway(layaway_id) if layaway_id is not None else None

    def _collect(self) -> None:
        layaway = self._selected()
        if layaway is None:
            show_error(self, "Pick a layaway first.", "Nothing selected")
            return
        if layaway["status"] != layaways_service.HELD:
            show_error(
                self, f"Layaway {layaway['reference']} is {layaway['status'].lower()}.",
                "Already finished",
            )
            return
        owing = layaways_service.outstanding(layaway["layaway_id"])
        fields = [
            {"key": "method", "label": "How the rest is paid", "type": "option",
             "values": list(config.PAYMENT_METHODS), "value": "Cash"},
            {"key": "amount", "label": f"Received now (owing: {fmt_usd(owing)})",
             "type": "number", "value": f"{owing:.2f}"},
        ]

        collected: list[int] = []

        def submit(values):
            collected.append(layaways_service.collect(
                layaway["layaway_id"], self.user.user_id,
                payment_method=values["method"], amount_paid=values["amount"],
            ))

        if FormModal(self, f"Collect {layaway['reference']}", fields, submit,
                     submit_text="Complete the sale").wait_result():
            self.reload()
            from app.services import receipts

            path = receipts.generate_receipt(collected[0])
            from app.ui import receipt_actions

            receipt_actions.open_file(path)

    def _cancel(self) -> None:
        layaway = self._selected()
        if layaway is None:
            show_error(self, "Pick a layaway first.", "Nothing selected")
            return
        if layaway["status"] != layaways_service.HELD:
            show_error(
                self, f"Layaway {layaway['reference']} is {layaway['status'].lower()}.",
                "Already finished",
            )
            return
        deposit = layaway["deposit_usd"]
        message = (
            f"Cancel {layaway['reference']}?\n\nThe goods go back on the shelf"
        )
        if deposit:
            message += f" and the {fmt_usd(deposit)} deposit is handed back in cash"
        if not ask_confirm(self, message + ".", "Cancel layaway"):
            return
        try:
            layaways_service.cancel(layaway["layaway_id"], self.user.user_id)
        except layaways_service.LayawayError as exc:
            show_error(self, exc, "Could not cancel")
            return
        self.reload()
