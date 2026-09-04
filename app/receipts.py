"""PDF receipts, return slips and till reports.

Everything here is drawn on a roll-shaped page whose height grows with the
number of lines, so a two-item receipt is not padded out to A4. The width comes
from the ``receipt_width_mm`` setting, because 58 mm and 80 mm thermal printers
are both common and a receipt laid out for one looks wrong on the other.

Totals are printed in USD and in LBP at the rate that was stored on the sale,
so reprinting an old invoice never re-prices it at today's rate.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from pathlib import Path

from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

from app import config
from app.money import D, fmt_lbp, fmt_usd, to_lbp
from app.services import returns as returns_service
from app.services import sales as sales_service
from app.services import settings as settings_service
from app.services import shifts as shifts_service

FONT = "Helvetica"
FONT_BOLD = "Helvetica-Bold"

# Style name -> (font, point size at 80 mm). Narrow rolls scale these down.
STYLES = {
    "title": (FONT_BOLD, 11),
    "bold": (FONT_BOLD, 8),
    "total": (FONT_BOLD, 10),
    "normal": (FONT, 8),
    "center": (FONT, 8),
    "small": (FONT, 7),
    "small-center": (FONT, 7),
}
CENTERED = ("title", "center", "small-center")


class ReceiptError(Exception):
    """Raised when a receipt cannot be produced."""


@dataclass(frozen=True)
class Layout:
    """The page geometry for one roll width."""

    width_mm: int
    page_width: float
    margin: float
    line: float
    scale: float

    @property
    def content_width(self) -> float:
        return self.page_width - 2 * self.margin

    def font(self, style: str) -> tuple[str, float]:
        font, size = STYLES.get(style, STYLES["normal"])
        return font, round(size * self.scale, 1)


def layout_for(width_mm=None) -> Layout:
    """Geometry for the configured (or requested) roll width."""
    if width_mm is None:
        width_mm = settings_service.receipt_width_mm()
    width_mm = 58 if int(width_mm) == 58 else 80
    if width_mm == 58:
        # A 58 mm roll prints about 48 mm wide; the margin has to shrink with it
        # or two thirds of the paper is wasted.
        return Layout(58, 58 * mm, 4 * mm, 3.7 * mm, 0.86)
    return Layout(80, 80 * mm, 5 * mm, 4.2 * mm, 1.0)


def default_path(name: str) -> Path:
    config.ensure_directories()
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    return config.RECEIPTS_DIR / f"{safe}.pdf"


def _wrap(text: str, font: str, size: float, width: float) -> list[str]:
    """Greedy word wrap, with a hard character break for unbroken strings."""
    words = str(text).split()
    if not words:
        return [""]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if stringWidth(candidate, font, size) <= width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)

    wrapped: list[str] = []
    for line in lines:
        while stringWidth(line, font, size) > width and len(line) > 1:
            cut = len(line)
            while cut > 1 and stringWidth(line[:cut], font, size) > width:
                cut -= 1
            wrapped.append(line[:cut])
            line = line[cut:]
        wrapped.append(line)
    return wrapped


class Builder:
    """Collects ``(style, left, right)`` rows, wrapping text as it goes."""

    def __init__(self, layout: Layout):
        self.layout = layout
        self.rows: list[tuple[str, str, str]] = []

    def add(self, style: str = "normal", left: str = "", right: str = "") -> None:
        self.rows.append((style, left, right))

    def rule(self) -> None:
        self.add("rule")

    def gap(self) -> None:
        self.add("gap")

    def wrapped(self, style: str, text: str) -> None:
        """Add ``text`` across as many lines as it needs."""
        font, size = self.layout.font(style)
        for line in _wrap(text, font, size, self.layout.content_width):
            self.add(style, line)

    def header(self, store: dict, title: str = "") -> None:
        self.wrapped("title", store["name"] or config.APP_NAME)
        for field in ("address", "phone"):
            if store.get(field):
                self.wrapped("small-center", store[field])
        if title:
            self.add("bold", title)
        self.rule()

    def footer(self, store: dict | None = None) -> None:
        self.gap()
        if store and store.get("footer"):
            self.wrapped("center", store["footer"])
        self.add("small-center", f"Printed {dt.datetime.now():%Y-%m-%d %H:%M}")


def render(builder: Builder, path: Path, title: str) -> Path:
    """Draw the collected rows onto a page sized to fit them."""
    layout = builder.layout
    rows = builder.rows
    height = layout.margin * 2 + layout.line * (len(rows) + 2)

    path.parent.mkdir(parents=True, exist_ok=True)
    pdf = canvas.Canvas(str(path), pagesize=(layout.page_width, height))
    pdf.setTitle(title)

    y = height - layout.margin
    for style, left, right in rows:
        y -= layout.line
        if style == "rule":
            pdf.setLineWidth(0.4)
            pdf.line(
                layout.margin, y + layout.line * 0.35,
                layout.page_width - layout.margin, y + layout.line * 0.35,
            )
            continue
        if style == "gap":
            continue

        font, size = layout.font(style)
        pdf.setFont(font, size)
        if style in CENTERED:
            pdf.drawCentredString(layout.page_width / 2, y, left)
        elif right:
            pdf.drawString(layout.margin, y, left)
            pdf.drawRightString(layout.page_width - layout.margin, y, right)
        else:
            pdf.drawString(layout.margin, y, left)

    pdf.showPage()
    pdf.save()
    return path


# --------------------------------------------------------------------------- #
# Sale receipt
# --------------------------------------------------------------------------- #

def generate_receipt(sale_id: int, path=None, width_mm=None) -> Path:
    """Render the receipt for ``sale_id`` and return the file path."""
    sale = sales_service.get_sale(sale_id)
    if sale is None:
        raise ReceiptError("Sale not found.")
    items = sales_service.get_sale_items(sale_id)
    store = settings_service.store_info()
    rounding = settings_service.lbp_rounding()
    rate = D(sale["exchange_rate"]) or settings_service.exchange_rate()

    builder = build_sale(sale, items, store, rate, rounding, layout_for(width_mm))
    path = Path(path) if path else default_path(sale["invoice_no"])
    return render(builder, path, f"Receipt {sale['invoice_no']}")


def build_sale(sale, items, store, rate, rounding, layout: Layout) -> Builder:
    """The receipt as rows. Separate from rendering so it can be inspected."""
    builder = Builder(layout)
    builder.header(store)

    builder.add("bold", "INVOICE", sale["invoice_no"])
    builder.add("small", "Date", str(sale["sale_time"]))
    builder.add("small", "Served by", sale["cashier_name"] or sale["cashier"] or "-")
    builder.add("small", "Customer", sale["customer_name"] or "Walk-in")

    status = sales_service.display_status(sale)
    if status != config.SALE_COMPLETED:
        builder.add("bold", f"*** {status.upper()} ***")
    builder.rule()

    builder.add("bold", "Item", "Amount")
    for item in items:
        builder.wrapped("normal", item["name_at_sale"])
        qty_label = f"   {item['qty']} x {fmt_usd(item['unit_price_usd'])}"
        if D(item["discount_usd"]) > 0:
            qty_label += f"  (-{fmt_usd(item['discount_usd'])})"
        builder.add("normal", qty_label, fmt_usd(item["line_total_usd"]))
        if item["returned_qty"]:
            builder.add("small", f"   {item['returned_qty']} returned")
    builder.rule()

    builder.add("normal", "Subtotal", fmt_usd(sale["subtotal_usd"]))
    if D(sale["discount_usd"]) > 0:
        builder.add("normal", "Discount", f"-{fmt_usd(sale['discount_usd'])}")
    if D(sale["tax_usd"]) > 0:
        builder.add("normal", "Tax", fmt_usd(sale["tax_usd"]))
    builder.add("total", "TOTAL USD", fmt_usd(sale["total_usd"]))
    builder.add("bold", "TOTAL LBP", fmt_lbp(to_lbp(sale["total_usd"], rate, rounding)))
    builder.add("small", "Rate", f"1 USD = {D(rate):,.0f} LBP")
    builder.rule()

    paid_display = (
        fmt_lbp(sale["amount_paid"])
        if sale["paid_currency"] == "LBP"
        else fmt_usd(sale["amount_paid"])
    )
    builder.add("normal", f"Paid ({sale['payment_method']})", paid_display)
    if D(sale["change_usd"]) > 0:
        builder.add("normal", "Change", fmt_usd(sale["change_usd"]))
        builder.add(
            "small", "Change (LBP)", fmt_lbp(to_lbp(sale["change_usd"], rate, rounding))
        )
    if sale["note"]:
        builder.gap()
        builder.wrapped("small", sale["note"])

    builder.footer(store)
    return builder


# --------------------------------------------------------------------------- #
# Return slip
# --------------------------------------------------------------------------- #

def generate_return_receipt(return_id: int, path=None, width_mm=None) -> Path:
    """A slip for the customer showing what was brought back and refunded."""
    record = returns_service.get_return(return_id)
    if record is None:
        raise ReceiptError("Return not found.")
    items = returns_service.get_return_items(return_id)
    store = settings_service.store_info()
    rounding = settings_service.lbp_rounding()
    rate = D(record["exchange_rate"]) or settings_service.exchange_rate()

    builder = build_return(record, items, store, rate, rounding, layout_for(width_mm))
    path = Path(path) if path else default_path(record["return_no"])
    return render(builder, path, f"Return {record['return_no']}")


def build_return(record, items, store, rate, rounding, layout: Layout) -> Builder:
    builder = Builder(layout)
    builder.header(store, "RETURN / REFUND")

    builder.add("bold", "RETURN", record["return_no"])
    builder.add("small", "Against invoice", record["invoice_no"] or "-")
    builder.add("small", "Date", str(record["created_at"]))
    builder.add("small", "Handled by", record["username"] or "-")
    if record["reason"]:
        builder.add("small", "Reason", record["reason"])
    if not record["restock"]:
        builder.add("small", "Goods not returned to stock")
    builder.rule()

    builder.add("bold", "Item", "Refund")
    for item in items:
        builder.wrapped("normal", item["name_at_sale"])
        builder.add(
            "normal",
            f"   {item['qty']} x {fmt_usd(item['unit_price_usd'])}",
            fmt_usd(item["line_total_usd"]),
        )
    builder.rule()

    builder.add("total", "REFUND USD", fmt_usd(record["total_usd"]))
    builder.add("bold", "REFUND LBP", fmt_lbp(to_lbp(record["total_usd"], rate, rounding)))
    builder.add("small", "Rate", f"1 USD = {D(rate):,.0f} LBP")
    builder.add("normal", "Refunded by", record["refund_method"])

    builder.footer(store)
    return builder


# --------------------------------------------------------------------------- #
# Till report
# --------------------------------------------------------------------------- #

def generate_shift_report(shift_id: int, path=None, kind: str = "Z", width_mm=None) -> Path:
    """An X (mid-shift snapshot) or Z (end of shift) till report.

    An X report is taken while the drawer is still open and changes nothing; a
    Z report is the closing statement. The numbers are identical — only the
    heading and the point in time differ.
    """
    kind = "X" if str(kind).upper() == "X" else "Z"
    shift = shifts_service.get_shift(shift_id)
    if shift is None:
        raise ReceiptError("Till shift not found.")

    totals = shifts_service.totals(shift_id)
    store = settings_service.store_info()
    rounding = settings_service.lbp_rounding()
    rate = D(shift["exchange_rate"]) or settings_service.exchange_rate()

    builder = build_shift_report(
        shift, totals, store, rate, rounding, kind, layout_for(width_mm)
    )
    path = Path(path) if path else default_path(f"{kind}-report-shift-{shift_id}")
    return render(builder, path, f"{kind} report - shift {shift_id}")


def build_shift_report(shift, totals, store, rate, rounding, kind, layout: Layout) -> Builder:
    shift_id = shift["shift_id"]
    builder = Builder(layout)
    builder.header(store, f"{kind} REPORT - TILL #{shift_id}")

    builder.add("small", "Opened", str(shift["opened_at"]))
    builder.add("small", "Opened by", shift["opened_by_name"] or "-")
    if shift["closed_at"]:
        builder.add("small", "Closed", str(shift["closed_at"]))
        builder.add("small", "Closed by", shift["closed_by_name"] or "-")
    else:
        builder.add("small", "Status", "Still open")
    builder.rule()

    builder.add("bold", "Sales")
    builder.add("normal", f"   {totals['sale_count']} sale(s)", fmt_usd(totals["sales_total"]))
    for row in shifts_service.payment_mix(shift_id):
        builder.add(
            "small", f"   {row['payment_method']} ({row['sale_count']})",
            fmt_usd(row["revenue"]),
        )
    if totals["return_count"]:
        builder.add(
            "normal", f"Returns ({totals['return_count']})",
            f"-{fmt_usd(totals['returns_total'])}",
        )
    builder.rule()

    builder.add("bold", "Cash drawer")
    builder.add("normal", "Opening float", fmt_usd(totals["opening_float"]))
    builder.add("normal", "Cash sales", fmt_usd(totals["cash_sales"]))
    # Money taken against a customer account is in the drawer too, and the
    # count will not reconcile unless the report says where it came from.
    if totals.get("cash_account_payments"):
        builder.add(
            "normal", "Account payments", fmt_usd(totals["cash_account_payments"])
        )
    if totals["cash_in"]:
        builder.add("normal", "Paid in", fmt_usd(totals["cash_in"]))
    if totals["cash_out"]:
        builder.add("normal", "Paid out", f"-{fmt_usd(totals['cash_out'])}")
    if totals["cash_returns"]:
        builder.add("normal", "Cash refunds", f"-{fmt_usd(totals['cash_returns'])}")
    builder.add("total", "EXPECTED", fmt_usd(totals["expected_usd"]))

    for movement in shifts_service.movements(shift_id):
        sign = "+" if movement["kind"] == config.CASH_IN else "-"
        builder.add(
            "small", f"   {movement['reason'] or movement['kind']}",
            f"{sign}{fmt_usd(movement['amount_usd'])}",
        )

    if shift["closed_at"]:
        builder.rule()
        builder.add("normal", "Counted USD", fmt_usd(shift["counted_usd"]))
        if shift["counted_lbp"]:
            builder.add("normal", "Counted LBP", fmt_lbp(shift["counted_lbp"]))
        builder.add("small", "Rate", f"1 USD = {D(rate):,.0f} LBP")
        variance = D(shift["variance_usd"])
        label = "OVER" if variance > 0 else ("SHORT" if variance < 0 else "BALANCED")
        builder.add("total", f"VARIANCE ({label})", fmt_usd(variance))
    else:
        builder.rule()
        builder.add("small", "Drawer not yet counted.")

    builder.add(
        "small", "Non-cash taken", fmt_usd(totals["non_cash_sales"])
    )
    non_cash_account = D(totals.get("account_payments", 0)) - D(
        totals.get("cash_account_payments", 0)
    )
    if non_cash_account:
        builder.add("small", "Account paid by card/transfer", fmt_usd(non_cash_account))
    builder.add("small", "In LBP", fmt_lbp(to_lbp(totals["expected_usd"], rate, rounding)))

    builder.footer()
    return builder
