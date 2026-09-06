"""Sales: building a cart, parking it, committing an invoice, refunding one."""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from dataclasses import dataclass, field
from decimal import Decimal

from app import config, db
from app.money import ZERO, D, compute_totals, to_float, usd
from app.money import line_total as compute_line_total
from app.services import accounts as accounts_service
from app.services import audit
from app.services import giftcards as giftcards_service
from app.services import products as products_service
from app.services import returns as returns_service
from app.services import settings as settings_service
from app.services import shifts as shifts_service


class SaleError(Exception):
    """Raised for user-facing sale failures."""


@dataclass
class CartLine:
    """One line in an in-progress sale, held in memory until checkout."""

    # A gift card line has no product behind it; its code rides along.
    product_id: int | None
    sku: str
    name: str
    qty: int
    unit_price: Decimal
    stock_available: int = 0
    discount: Decimal = field(default_factory=lambda: ZERO)
    list_price: Decimal = field(default_factory=lambda: ZERO)
    # This category's tax percentage at the time the line was added; None
    # means the store-wide rate. Taken when the line is added, the way the
    # cost is, so a category rate changed mid-sale does not rewrite the cart.
    tax_rate: Decimal | None = None
    gift_code: str | None = None

    @property
    def is_gift_card(self) -> bool:
        return self.gift_code is not None

    @property
    def line_total(self) -> Decimal:
        return compute_line_total(self.qty, self.unit_price, self.discount)

    @property
    def gross_total(self) -> Decimal:
        return usd(D(self.qty) * self.unit_price)

    @property
    def price_overridden(self) -> bool:
        return self.list_price > ZERO and self.unit_price != self.list_price


class Cart:
    """The working sale. Deliberately UI-agnostic so it can be unit tested."""

    def __init__(self):
        self.lines: list[CartLine] = []
        self.discount: Decimal = ZERO
        self.customer_id: int | None = None
        self.note: str = ""
        # Products dropped when a parked sale was resumed (deleted or out of stock).
        self.unavailable: list = []
        # Gift card lines have no product behind them; they take ids counting
        # down from -1 so they never collide with a real product or each other.
        self._next_synthetic_id = -1

    # -- line management ---------------------------------------------------- #

    def find(self, product_id: int) -> CartLine | None:
        return next(
            (line for line in self.lines if line.product_id == product_id), None
        )

    def add_product(self, product: sqlite3.Row, qty: int = 1) -> CartLine:
        if qty <= 0:
            raise SaleError("Quantity must be at least 1.")
        existing = self.find(product["product_id"])
        wanted = (existing.qty if existing else 0) + qty
        if wanted > product["stock_qty"]:
            raise SaleError(
                f"Only {product['stock_qty']} × {product['name']} left in stock."
            )
        if existing:
            existing.qty = wanted
            return existing
        # Some callers hand in rows that did not select the category rate;
        # a sqlite3.Row has no __contains__, so the columns are checked by name.
        columns = product.keys()
        rate = (
            product["category_tax_rate"]
            if "category_tax_rate" in columns and product["category_tax_rate"] is not None
            else None
        )
        line = CartLine(
            product_id=product["product_id"],
            sku=product["sku"],
            name=product["name"],
            qty=qty,
            unit_price=usd(product["price_usd"]),
            stock_available=product["stock_qty"],
            list_price=usd(product["price_usd"]),
            tax_rate=D(rate) if rate is not None else None,
        )
        self.lines.append(line)
        return line

    def add_gift_card(self, code: str, amount) -> CartLine:
        """A card the customer is buying now; it comes alive at checkout."""
        value = usd(D(amount))
        if value <= ZERO:
            raise SaleError("A gift card has to be loaded with something.")
        code = giftcards_service.normalise(code)
        self._next_synthetic_id -= 1
        line = CartLine(
            product_id=self._next_synthetic_id,
            sku="GIFT",
            name=f"Gift card {code}",
            qty=1,
            unit_price=value,
            stock_available=10**9,
            list_price=value,
            gift_code=code,
        )
        self.lines.append(line)
        return line

    def set_qty(self, product_id: int, qty: int) -> None:
        line = self.find(product_id)
        if line is None:
            return
        if qty <= 0:
            self.remove(product_id)
            return
        if qty > line.stock_available:
            raise SaleError(f"Only {line.stock_available} × {line.name} left in stock.")
        line.qty = qty

    def set_price(self, product_id: int, price) -> None:
        """Override a line's unit price (an admin-gated action in the UI)."""
        line = self.find(product_id)
        if line is None:
            return
        price = usd(price)
        if price < ZERO:
            raise SaleError("Price cannot be negative.")
        line.unit_price = price

    def set_line_discount(self, product_id: int, amount=None, percent=None) -> None:
        """Discount one line by an absolute amount or a percentage of its value."""
        line = self.find(product_id)
        if line is None:
            return
        if percent is not None:
            percent = D(percent)
            if percent < ZERO or percent > 100:
                raise SaleError("A line discount must be between 0% and 100%.")
            amount = line.gross_total * percent / D(100)
        amount = usd(max(ZERO, D(amount or 0)))
        if amount > line.gross_total:
            raise SaleError(
                f"A discount of {usd(amount)} is more than the line is worth "
                f"({usd(line.gross_total)})."
            )
        line.discount = amount

    def remove(self, product_id: int) -> None:
        self.lines = [
            line for line in self.lines if line.product_id != product_id
        ]

    def clear(self) -> None:
        self.lines.clear()
        self.discount = ZERO
        self.customer_id = None
        self.note = ""
        self._next_synthetic_id = -1

    # -- totals ------------------------------------------------------------- #

    @property
    def item_count(self) -> int:
        return sum(line.qty for line in self.lines)

    @property
    def is_empty(self) -> bool:
        return not self.lines

    @property
    def line_discount_total(self) -> Decimal:
        return usd(sum((line.discount for line in self.lines), ZERO))

    def totals(self, tax_rate=None):
        if tax_rate is None:
            tax_rate = settings_service.tax_rate()
        return compute_totals(
            [
                (line.qty, line.unit_price, line.discount, line.tax_rate)
                for line in self.lines
            ],
            discount=self.discount,
            tax_rate=tax_rate,
        )

    # -- parking ------------------------------------------------------------ #

    def to_payload(self) -> str:
        return json.dumps({
            "customer_id": self.customer_id,
            "discount": str(self.discount),
            "note": self.note,
            "lines": [
                {
                    "product_id": line.product_id,
                    "qty": line.qty,
                    "unit_price": str(line.unit_price),
                    "discount": str(line.discount),
                    "gift_code": line.gift_code,
                }
                for line in self.lines
            ],
        })

    @classmethod
    def from_payload(cls, payload: str) -> Cart:
        """Rebuild a parked cart, re-reading each product for today's stock."""
        data = json.loads(payload)
        cart = cls()
        cart.customer_id = data.get("customer_id")
        cart.discount = D(data.get("discount", 0))
        cart.note = data.get("note", "")
        missing = []
        for entry in data.get("lines", []):
            if entry.get("gift_code"):
                cart.add_gift_card(entry["gift_code"], entry.get("unit_price", 0))
                continue
            product = products_service.get_product(entry["product_id"])
            if product is None or not product["is_active"]:
                missing.append(entry["product_id"])
                continue
            qty = min(int(entry["qty"]), product["stock_qty"])
            if qty <= 0:
                missing.append(product["name"])
                continue
            line = cart.add_product(product, qty)
            line.unit_price = usd(entry.get("unit_price", line.unit_price))
            line.discount = usd(entry.get("discount", 0))
        cart.unavailable = missing
        return cart


def park_sale(cart: Cart, user_id: int, label: str = "") -> int:
    if cart.is_empty:
        raise SaleError("There is nothing in the cart to park.")
    label = (label or "").strip() or f"Held {dt.datetime.now():%H:%M}"
    parked_id = db.execute(
        "INSERT INTO parked_sales (label, user_id, payload) VALUES (?, ?, ?)",
        (label, user_id, cart.to_payload()),
    )
    audit.record("Sale parked", "parked_sale", parked_id,
                 f"{label}: {cart.item_count} item(s)")
    return parked_id


def list_parked() -> list[sqlite3.Row]:
    return db.query(
        """
        SELECT p.*, u.username
        FROM parked_sales p
        LEFT JOIN users u ON u.user_id = p.user_id
        ORDER BY p.parked_id DESC
        """
    )


def resume_parked(parked_id: int) -> Cart:
    row = db.query_one("SELECT * FROM parked_sales WHERE parked_id = ?", (parked_id,))
    if row is None:
        raise SaleError("That held sale no longer exists.")
    try:
        cart = Cart.from_payload(row["payload"])
    except (ValueError, KeyError) as exc:
        raise SaleError("That held sale could not be read.") from exc
    db.execute("DELETE FROM parked_sales WHERE parked_id = ?", (parked_id,))
    return cart


def delete_parked(parked_id: int) -> None:
    db.execute("DELETE FROM parked_sales WHERE parked_id = ?", (parked_id,))


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #

def next_invoice_no(conn: sqlite3.Connection) -> str:
    """``INV-YYYYMMDD-0001``, sequential within the day.

    Derived from the highest existing number for today rather than a row count,
    so deleting or refunding a sale can never hand out a duplicate.
    """
    today = dt.date.today().strftime("%Y%m%d")
    prefix = f"INV-{today}-"
    row = conn.execute(
        "SELECT MAX(invoice_no) AS last FROM sales WHERE invoice_no LIKE ?",
        (prefix + "%",),
    ).fetchone()
    sequence = 1
    if row and row["last"]:
        try:
            sequence = int(str(row["last"]).rsplit("-", 1)[1]) + 1
        except (ValueError, IndexError):
            sequence = 1
    return f"{prefix}{sequence:04d}"


def create_sale(
    *,
    user_id: int,
    cart: Cart,
    customer_id: int | None = None,
    payment_method: str = "Cash",
    paid_currency: str = "USD",
    amount_paid=0,
    note: str = "",
    tax_rate=None,
    exchange_rate=None,
    gift_card_code: str = "",
    gift_card_amount=None,
    paid_already=0,
) -> int:
    """Commit a cart as an invoice. Returns the new ``sale_id``.

    Stock is re-checked against the database inside the transaction, so two
    tills racing for the last unit cannot both succeed. A gift card pays what
    it holds towards the total first — its balance is re-read and re-checked
    inside that same transaction — and the rest is taken by the chosen
    payment method as usual. ``paid_already`` is money taken for this invoice
    before today's till work began (a layaway's deposit): the due amount and
    the change are computed against what is still owing, and the invoice
    records everything it has received across both moments.
    """
    if cart.is_empty:
        raise SaleError("Add at least one product before completing the sale.")
    if payment_method not in config.PAYMENT_METHODS:
        raise SaleError(f"Unknown payment method: {payment_method}")
    if paid_currency not in config.CURRENCIES:
        raise SaleError(f"Unknown currency: {paid_currency}")

    shift = shifts_service.require_open_shift()

    if tax_rate is None:
        tax_rate = settings_service.tax_rate()
    if exchange_rate is None:
        exchange_rate = settings_service.exchange_rate()
    exchange_rate = D(exchange_rate)
    if exchange_rate <= ZERO:
        raise SaleError("The USD → LBP exchange rate must be greater than zero.")

    subtotal, discount, tax, total = cart.totals(tax_rate=tax_rate)

    # A gift card spends itself first; whatever is left is the payment the
    # chosen method has to cover. The balance is re-read inside the
    # transaction below — this pass only decides what is due from the till.
    gift_code = (gift_card_code or "").strip()
    gift_applied = ZERO
    if gift_code:
        if payment_method == "Credit":
            raise SaleError("A gift card cannot be combined with credit on account.")
        if any(line.is_gift_card for line in cart.lines):
            raise SaleError("A gift card cannot pay for another gift card.")
        card = db.query_one(
            "SELECT * FROM gift_cards WHERE code = ?", (giftcards_service.normalise(gift_code),)
        )
        if card is None:
            raise SaleError(f"No gift card matches '{gift_code}'.")
        if card["status"] == giftcards_service.DISABLED:
            raise SaleError(f"Gift card {card['code']} has been disabled.")
        balance = D(card["balance_usd"])
        if balance <= ZERO or card["status"] == giftcards_service.EMPTY:
            raise SaleError(f"Gift card {card['code']} is empty.")
        wanted = balance if gift_card_amount is None else D(gift_card_amount)
        gift_applied = usd(min(wanted, balance, total))
        if gift_applied <= ZERO:
            raise SaleError("There is nothing on this sale for the card to pay for.")
    remaining_due = total - gift_applied
    already = usd(max(ZERO, D(paid_already)))
    remaining_due = usd(remaining_due - already)

    if customer_id is None:
        customer_id = cart.customer_id

    paid = D(amount_paid)
    paid_usd = usd(paid / exchange_rate) if paid_currency == "LBP" else usd(paid)
    if payment_method == "Cash" and paid_usd < remaining_due:
        raise SaleError(
            f"Cash received ({paid_usd}) is less than the total due ({remaining_due})"
            + (f" after the gift card's {gift_applied}." if gift_applied else ".")
        )
    change = usd(max(ZERO, paid_usd - remaining_due))

    if payment_method == "Credit":
        # Nothing is handed over, so nothing is recorded as paid — the invoice
        # becomes a debt instead. Checked before the sale is written, so a
        # customer over their limit costs the cashier a payment method, not a
        # half-committed invoice.
        try:
            accounts_service.check_can_charge(customer_id, remaining_due)
        except accounts_service.AccountError as exc:
            raise SaleError(str(exc)) from exc
        paid = ZERO
        paid_usd = ZERO
        change = ZERO

    with db.transaction() as conn:
        invoice_no = next_invoice_no(conn)
        cursor = conn.execute(
            """
            INSERT INTO sales
                (invoice_no, customer_id, user_id, shift_id, subtotal_usd, discount_usd,
                 tax_usd, total_usd, exchange_rate, payment_method, paid_currency,
                 amount_paid, change_usd, status, note)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                invoice_no,
                customer_id,
                user_id,
                shift["shift_id"] if shift else None,
                to_float(subtotal),
                to_float(discount),
                to_float(tax),
                to_float(total),
                float(exchange_rate),
                payment_method,
                paid_currency,
                float(paid + already),
                to_float(change),
                config.SALE_COMPLETED,
                (note or cart.note or "").strip(),
            ),
        )
        sale_id = cursor.lastrowid

        overrides = []
        for line in cart.lines:
            if line.is_gift_card:
                # The card on the receipt is a line like any other; the card
                # itself comes to life here, in the sale's transaction, so a
                # rolled-back sale never leaves a live card behind.
                assert line.gift_code is not None
                conn.execute(
                    """
                    INSERT INTO sale_items
                        (sale_id, product_id, sku_at_sale, name_at_sale, qty,
                         unit_price_usd, cost_usd, discount_usd, line_total_usd)
                    VALUES (?, NULL, 'GIFT', ?, 1, ?, 0, 0, ?)
                    """,
                    (
                        sale_id,
                        line.name,
                        to_float(line.unit_price),
                        to_float(line.line_total),
                    ),
                )
                giftcards_service.activate(
                    conn, line.gift_code, line.unit_price, sale_id, user_id
                )
                continue
            product = conn.execute(
                "SELECT name, sku, cost_usd, stock_qty, price_usd FROM products WHERE product_id = ?",
                (line.product_id,),
            ).fetchone()
            if product is None:
                raise SaleError(f"'{line.name}' no longer exists in the catalogue.")
            if product["stock_qty"] < line.qty:
                raise SaleError(
                    f"Only {product['stock_qty']} × {product['name']} left in stock."
                )
            if usd(product["price_usd"]) != line.unit_price:
                overrides.append(
                    f"{product['name']} {usd(product['price_usd'])} → {line.unit_price}"
                )
            conn.execute(
                """
                INSERT INTO sale_items
                    (sale_id, product_id, sku_at_sale, name_at_sale, qty,
                     unit_price_usd, cost_usd, discount_usd, line_total_usd)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sale_id,
                    line.product_id,
                    product["sku"],
                    product["name"],
                    line.qty,
                    to_float(line.unit_price),
                    float(product["cost_usd"]),
                    to_float(line.discount),
                    to_float(line.line_total),
                ),
            )
            products_service.adjust_stock(
                line.product_id,
                -line.qty,
                reason="Sale",
                user_id=user_id,
                note=invoice_no,
                sale_id=sale_id,
                conn=conn,
            )

        if gift_applied > ZERO:
            # Same transaction as the invoice: the card's balance and the sale
            # that spent it must both land, or neither.
            giftcards_service.redeem(conn, gift_code, gift_applied, sale_id, user_id)

        if payment_method == "Credit":
            # Same transaction as the invoice: a sale on account and the debt it
            # creates must both exist, or neither.
            accounts_service.charge_sale(
                customer_id, sale_id, total,
                invoice_no=invoice_no, user_id=user_id,
                shift_id=shift["shift_id"] if shift else None,
                conn=conn,
            )

    if overrides:
        audit.record("Price overridden", "sale", sale_id,
                     f"{invoice_no}: " + "; ".join(overrides))
    if discount > ZERO or cart.line_discount_total > ZERO:
        audit.record(
            "Discount given", "sale", sale_id,
            f"{invoice_no}: invoice {usd(discount)}, lines {cart.line_discount_total}",
        )
    return sale_id


def refund_sale(sale_id: int, user_id: int, reason: str = "") -> None:
    """Refund an entire invoice. Partial returns go through ``returns`` instead."""
    sale = db.query_one("SELECT * FROM sales WHERE sale_id = ?", (sale_id,))
    if sale is None:
        raise SaleError("Sale not found.")
    if sale["status"] == config.SALE_REFUNDED:
        raise SaleError(f"Invoice {sale['invoice_no']} has already been refunded.")
    try:
        returns_service.return_everything(sale_id, user_id, reason=reason)
    except returns_service.ReturnError as exc:
        raise SaleError(str(exc)) from exc


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #

# Aggregates are pre-joined rather than correlated per row: a search that
# returns 500 invoices used to run 1,500 sub-queries to say how many items and
# how much of each had come back. One grouped pass over the two child tables,
# joined on, answers every row at once.
_SALE_SELECT = """
    SELECT s.*, c.name AS customer_name, u.username AS cashier,
           u.full_name AS cashier_name,
           COALESCE(ic.item_count, 0) AS item_count,
           COALESCE(ic.returned_count, 0) AS returned_count,
           COALESCE(rf.refunded_usd, 0) AS refunded_usd
    FROM sales s
    LEFT JOIN customers c ON c.customer_id = s.customer_id
    LEFT JOIN users u ON u.user_id = s.user_id
    LEFT JOIN (
        SELECT sale_id,
               SUM(qty) AS item_count,
               SUM(returned_qty) AS returned_count
        FROM sale_items
        GROUP BY sale_id
    ) ic ON ic.sale_id = s.sale_id
    LEFT JOIN (
        SELECT sale_id, SUM(total_usd) AS refunded_usd
        FROM returns
        GROUP BY sale_id
    ) rf ON rf.sale_id = s.sale_id
"""


def get_sale(sale_id: int) -> sqlite3.Row | None:
    return db.query_one(_SALE_SELECT + " WHERE s.sale_id = ?", (sale_id,))


def get_sale_items(sale_id: int) -> list[sqlite3.Row]:
    return db.query(
        "SELECT * FROM sale_items WHERE sale_id = ? ORDER BY sale_item_id", (sale_id,)
    )


def display_status(sale) -> str:
    """'Completed', 'Refunded', or 'Part returned' derived from the line counts."""
    if sale["status"] == config.SALE_REFUNDED:
        return config.SALE_REFUNDED
    # .keys() rather than `in sale`: a sqlite3.Row has no __contains__, and
    # this row may come from a query that did not select the column.
    returned = (
        sale["returned_count"] if "returned_count" in sale.keys() else 0  # noqa: SIM118
    )
    return "Part returned" if returned else config.SALE_COMPLETED


def list_sales(
    date_from: str | None = None,
    date_to: str | None = None,
    search: str = "",
    status: str | None = None,
    customer_id: int | None = None,
    shift_id: int | None = None,
    limit: int = 500,
) -> list[sqlite3.Row]:
    clauses: list[str] = []
    params: list = []

    date_clauses, date_params = db.date_range_clauses("s.sale_time", date_from, date_to)
    clauses += date_clauses
    params += date_params
    if status:
        clauses.append("s.status = ?")
        params.append(status)
    if customer_id:
        clauses.append("s.customer_id = ?")
        params.append(customer_id)
    if shift_id:
        clauses.append("s.shift_id = ?")
        params.append(shift_id)
    if search and search.strip():
        clauses.append("(s.invoice_no LIKE ? OR c.name LIKE ? OR u.username LIKE ?)")
        pattern = f"%{search.strip()}%"
        params += [pattern, pattern, pattern]

    sql = _SALE_SELECT
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY s.sale_id DESC LIMIT ?"
    params.append(limit)
    return db.query(sql, tuple(params))
