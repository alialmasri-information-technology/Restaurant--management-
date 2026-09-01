"""PDF receipt generation.

Produces an 80 mm thermal-printer-shaped page whose height grows with the
number of lines, so a two-item receipt is not padded out to A4. Totals are
printed in USD and in LBP at the rate that was stored on the sale, so
reprinting an old invoice never re-prices it at today's rate.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

from app import config
from app.money import D, fmt_lbp, fmt_usd, to_lbp
from app.services import sales as sales_service
from app.services import settings as settings_service

PAGE_WIDTH = 80 * mm
MARGIN = 5 * mm
CONTENT_WIDTH = PAGE_WIDTH - 2 * MARGIN
LINE = 4.2 * mm

FONT = "Helvetica"
FONT_BOLD = "Helvetica-Bold"


class ReceiptError(Exception):
    """Raised when a receipt cannot be produced."""


def default_path(invoice_no: str) -> Path:
    config.ensure_directories()
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", invoice_no)
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


def generate_receipt(sale_id: int, path=None) -> Path:
    """Render the receipt for ``sale_id`` and return the file path."""
    sale = sales_service.get_sale(sale_id)
    if sale is None:
        raise ReceiptError("Sale not found.")
    items = sales_service.get_sale_items(sale_id)
    store = settings_service.store_info()
    rounding = settings_service.lbp_rounding()
    rate = D(sale["exchange_rate"]) or settings_service.exchange_rate()

    path = Path(path) if path else default_path(sale["invoice_no"])
    path.parent.mkdir(parents=True, exist_ok=True)

    # Two passes: measure the content, then draw it on a page of that height.
    body = _build_lines(sale, items, store, rate, rounding)
    height = MARGIN * 2 + LINE * (len(body) + 2)
    pdf = canvas.Canvas(str(path), pagesize=(PAGE_WIDTH, height))
    pdf.setTitle(f"Receipt {sale['invoice_no']}")

    y = height - MARGIN
    for kind, left, right in body:
        y -= LINE
        if kind == "rule":
            pdf.setLineWidth(0.4)
            pdf.line(MARGIN, y + LINE * 0.35, PAGE_WIDTH - MARGIN, y + LINE * 0.35)
            continue
        if kind == "gap":
            continue

        font, size = {
            "title": (FONT_BOLD, 11),
            "bold": (FONT_BOLD, 8),
            "total": (FONT_BOLD, 10),
            "small": (FONT, 7),
        }.get(kind, (FONT, 8))
        pdf.setFont(font, size)

        if kind in ("title", "center", "small-center"):
            if kind == "small-center":
                pdf.setFont(FONT, 7)
            pdf.drawCentredString(PAGE_WIDTH / 2, y, left)
        elif right:
            pdf.drawString(MARGIN, y, left)
            pdf.drawRightString(PAGE_WIDTH - MARGIN, y, right)
        else:
            pdf.drawString(MARGIN, y, left)

    pdf.showPage()
    pdf.save()
    return path


def _build_lines(sale, items, store, rate, rounding) -> list[tuple[str, str, str]]:
    """Flatten the receipt into ``(style, left_text, right_text)`` rows."""
    out: list[tuple[str, str, str]] = []

    def add(kind, left="", right=""):
        out.append((kind, left, right))

    add("title", store["name"] or config.APP_NAME)
    for field in ("address", "phone"):
        if store.get(field):
            for line in _wrap(store[field], FONT, 7, CONTENT_WIDTH):
                add("small-center", line)
    add("rule")

    add("bold", "INVOICE", sale["invoice_no"])
    add("small", "Date", str(sale["sale_time"]))
    add("small", "Served by", sale["cashier_name"] or sale["cashier"] or "—")
    add("small", "Customer", sale["customer_name"] or "Walk-in")
    if sale["status"] != config.SALE_COMPLETED:
        add("bold", f"*** {sale['status'].upper()} ***")
    add("rule")

    add("bold", "Item", "Amount")
    for item in items:
        for line in _wrap(item["name_at_sale"], FONT, 8, CONTENT_WIDTH):
            add("normal", line)
        qty_label = f"   {item['qty']} × {fmt_usd(item['unit_price_usd'])}"
        add("normal", qty_label, fmt_usd(item["line_total_usd"]))
    add("rule")

    add("normal", "Subtotal", fmt_usd(sale["subtotal_usd"]))
    if D(sale["discount_usd"]) > 0:
        add("normal", "Discount", f"-{fmt_usd(sale['discount_usd'])}")
    if D(sale["tax_usd"]) > 0:
        add("normal", "Tax", fmt_usd(sale["tax_usd"]))
    add("total", "TOTAL USD", fmt_usd(sale["total_usd"]))
    add("bold", "TOTAL LBP", fmt_lbp(to_lbp(sale["total_usd"], rate, rounding)))
    add("small", "Rate", f"1 USD = {D(rate):,.0f} LBP")
    add("rule")

    paid_currency = sale["paid_currency"]
    paid_display = (
        fmt_lbp(sale["amount_paid"])
        if paid_currency == "LBP"
        else fmt_usd(sale["amount_paid"])
    )
    add("normal", f"Paid ({sale['payment_method']})", paid_display)
    if D(sale["change_usd"]) > 0:
        add("normal", "Change", fmt_usd(sale["change_usd"]))
        add("small", "Change (LBP)", fmt_lbp(to_lbp(sale["change_usd"], rate, rounding)))
    if sale["note"]:
        add("gap")
        for line in _wrap(sale["note"], FONT, 7, CONTENT_WIDTH):
            add("small", line)

    add("gap")
    if store.get("footer"):
        for line in _wrap(store["footer"], FONT, 8, CONTENT_WIDTH):
            add("center", line)
    add("small-center", f"Printed {dt.datetime.now():%Y-%m-%d %H:%M}")
    return out
