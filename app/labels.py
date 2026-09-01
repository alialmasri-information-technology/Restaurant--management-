"""Barcode label sheets.

Labels are laid out on whatever the shop actually feeds the printer: a grid on
an A4 sticker sheet, or one label per page on a thermal roll. The barcode is
Code128, which encodes the letters and dashes real SKUs are full of — an
EAN-13 symbology would reject everything except a 13-digit number.

A product with no barcode of its own is labelled with its SKU, so the scanner
still finds it: ``products.get_by_code`` matches either.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from pathlib import Path

from reportlab.graphics.barcode import code128
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

from app import config
from app.money import D, fmt_lbp, fmt_usd, to_lbp
from app.services import products as products_service
from app.services import settings as settings_service

FONT = "Helvetica"
FONT_BOLD = "Helvetica-Bold"

MAX_LABELS = 2000  # a runaway quantity would produce a hundred-megabyte PDF
MIN_BAR_WIDTH = 0.125 * mm  # one dot on a 203 dpi thermal head


class LabelError(Exception):
    """Raised when a label sheet cannot be produced."""


@dataclass(frozen=True)
class LabelSheet:
    """One physical stationery format."""

    key: str
    title: str
    page_size: tuple
    columns: int
    rows: int
    label_width: float
    label_height: float
    margin_x: float
    margin_y: float

    @property
    def per_page(self) -> int:
        return self.columns * self.rows


SHEETS = {
    "a4-24": LabelSheet(
        key="a4-24",
        title="A4 sheet - 24 labels (63.5 x 33.9 mm)",
        page_size=A4,
        columns=3, rows=8,
        label_width=63.5 * mm, label_height=33.9 * mm,
        margin_x=9.75 * mm, margin_y=12.9 * mm,
    ),
    "a4-12": LabelSheet(
        key="a4-12",
        title="A4 sheet - 12 labels (99.1 x 42.3 mm)",
        page_size=A4,
        columns=2, rows=6,
        label_width=99.1 * mm, label_height=42.3 * mm,
        margin_x=5.9 * mm, margin_y=21.5 * mm,
    ),
    "roll-50x30": LabelSheet(
        key="roll-50x30",
        title="Thermal roll - one 50 x 30 mm label per page",
        page_size=(50 * mm, 30 * mm),
        columns=1, rows=1,
        label_width=50 * mm, label_height=30 * mm,
        margin_x=0, margin_y=0,
    ),
}

DEFAULT_SHEET = "a4-24"


def sheet_choices() -> list[tuple[str, str]]:
    """``(key, title)`` pairs for a dropdown."""
    return [(sheet.key, sheet.title) for sheet in SHEETS.values()]


def default_path() -> Path:
    config.ensure_directories()
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    return config.RECEIPTS_DIR / f"labels-{stamp}.pdf"


def code_for(product) -> str:
    """What the scanner should read: the barcode, or the SKU as a stand-in."""
    return (product["barcode"] or "").strip() or (product["sku"] or "").strip()


def build_items(quantities: dict) -> list[dict]:
    """Turn ``{product_id: how_many_labels}`` into rows ready to draw."""
    items = []
    for product_id, count in quantities.items():
        count = int(count)
        if count <= 0:
            continue
        product = products_service.get_product(int(product_id))
        if product is None:
            continue
        code = code_for(product)
        if not code:
            raise LabelError(
                f"{product['name']} has neither a barcode nor a SKU, so it "
                f"cannot be labelled."
            )
        items.append({
            "name": product["name"],
            "code": code,
            "price_usd": product["price_usd"],
            "count": count,
        })
    if not items:
        raise LabelError("Choose at least one product to print labels for.")

    total = sum(item["count"] for item in items)
    if total > MAX_LABELS:
        raise LabelError(
            f"That is {total:,} labels. Print at most {MAX_LABELS:,} at a time."
        )
    return items


def _fit(text: str, font: str, size: float, width: float) -> str:
    """Trim with an ellipsis until the text fits."""
    text = re.sub(r"\s+", " ", str(text)).strip()
    if stringWidth(text, font, size) <= width:
        return text
    while text and stringWidth(text + "...", font, size) > width:
        text = text[:-1]
    return (text + "...") if text else ""


def _barcode(value: str, max_width: float, height: float, label: str = ""):
    """A Code128 scaled to sit inside ``max_width``.

    The quiet zone is set explicitly at ten modules — the Code128 minimum —
    because reportlab otherwise pads every symbol with a fixed quarter-inch on
    each side. That fixed padding does not shrink with the bars, so a symbol
    could never be scaled down to fit a small label. With the quiet zone
    proportional, the total width is linear in the bar width and one rescale
    lands exactly on the target.

    Narrow bars stop being readable somewhere around one printer dot, so
    MIN_BAR_WIDTH is a single dot at 203 dpi. A code that will not fit even at
    that width would print as an unscannable smear, so it is refused with the
    advice that actually helps: use a bigger label.
    """
    def build(bar_width: float):
        quiet = 10 * bar_width
        return code128.Code128(
            value, barHeight=height, barWidth=bar_width, humanReadable=False,
            quiet=1, lquiet=quiet, rquiet=quiet,
        )

    nominal = 0.38 * mm
    symbol = build(nominal)
    if symbol.width <= max_width:
        return symbol

    scaled = nominal * (max_width / symbol.width)
    if scaled < MIN_BAR_WIDTH:
        raise LabelError(
            f"The barcode '{value}' is too long for this label size"
            + (f" ({label})" if label else "")
            + ". Choose a larger label format, or give the product a shorter code."
        )
    return build(scaled)


def generate_labels(
    quantities: dict,
    path=None,
    sheet: str = DEFAULT_SHEET,
    show_price: bool = True,
    show_lbp: bool = False,
    show_store: bool = False,
) -> Path:
    """Render a label sheet and return the file path."""
    spec = SHEETS.get(sheet)
    if spec is None:
        raise LabelError(f"Unknown label format: {sheet}")

    items = build_items(quantities)
    path = Path(path) if path else default_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    store = settings_service.store_info()["name"] if show_store else ""
    rate = settings_service.exchange_rate()
    rounding = settings_service.lbp_rounding()

    # One entry per physical label.
    flat = [item for item in items for _ in range(item["count"])]

    pdf = canvas.Canvas(str(path), pagesize=spec.page_size)
    pdf.setTitle(f"{config.APP_NAME} labels")

    page_height = spec.page_size[1]
    for index, item in enumerate(flat):
        position = index % spec.per_page
        if index and position == 0:
            pdf.showPage()

        column = position % spec.columns
        row = position // spec.columns
        x = spec.margin_x + column * spec.label_width
        y = page_height - spec.margin_y - (row + 1) * spec.label_height
        _draw_label(pdf, item, x, y, spec, show_price, show_lbp, store, rate, rounding)

    pdf.showPage()
    pdf.save()
    return path


def _draw_label(pdf, item, x, y, spec, show_price, show_lbp, store, rate, rounding):
    pad = 2 * mm
    inner_width = spec.label_width - 2 * pad
    centre = x + spec.label_width / 2
    top = y + spec.label_height - pad

    if store:
        pdf.setFont(FONT, 5.5)
        pdf.drawCentredString(centre, top - 4, _fit(store, FONT, 5.5, inner_width))
        top -= 6

    pdf.setFont(FONT_BOLD, 7.5)
    pdf.drawCentredString(centre, top - 6, _fit(item["name"], FONT_BOLD, 7.5, inner_width))
    top -= 10

    price_height = 11 if show_price else 0
    text_height = 7
    bar_height = max(6 * mm, (top - y - pad) - price_height - text_height)

    symbol = _barcode(item["code"], inner_width, bar_height, item["name"])
    bar_y = y + pad + price_height + text_height
    symbol.drawOn(pdf, centre - symbol.width / 2, bar_y)

    pdf.setFont(FONT, 6)
    pdf.drawCentredString(centre, bar_y - 6, item["code"])

    if show_price:
        price = fmt_usd(item["price_usd"])
        if show_lbp:
            price = f"{price} / {fmt_lbp(to_lbp(D(item['price_usd']), rate, rounding))}"
        pdf.setFont(FONT_BOLD, 9)
        pdf.drawCentredString(centre, y + pad, _fit(price, FONT_BOLD, 9, inner_width))
