"""Layaways: goods set aside, paid over time, collected as a normal sale.

The three things a layaway promises, kept honestly:

* **The price does not move.** Lines and total are frozen when the goods are
  held, so next month's price change cannot quietly rewrite somebody's
  agreement.
* **The deposit is real money.** A cash deposit goes into the drawer and is
  recorded as a cash movement like any other money the till holds; collecting
  the rest does not count it twice.
* **The shelves tell the truth.** Stock is not decremented while goods sit in
  the back room — the number on the shelf is the number that can still be
  sold to a walk-in. Collection runs the sale through the same stock check as
  any other sale, so goods that sold in the meantime stop the collection with
  an explanation instead of an oversold invoice.

A card deposit needs no drawer entry; the card terminal is its own record.
"""

from __future__ import annotations

import datetime as dt
import sqlite3

from app import config, db, logs
from app.money import ZERO, D, to_float, usd
from app.services import audit
from app.services import products as products_service
from app.services import sales as sales_service
from app.services import shifts as shifts_service

HELD = "Held"
COLLECTED = "Collected"
CANCELLED = "Cancelled"
STATUSES = (HELD, COLLECTED, CANCELLED)


class LayawayError(Exception):
    """Raised for user-facing layaway failures."""


def next_reference(conn: sqlite3.Connection) -> str:
    today = dt.date.today().strftime("%Y%m%d")
    prefix = f"LAY-{today}-"
    row = conn.execute(
        "SELECT MAX(reference) AS last FROM layaways WHERE reference LIKE ?",
        (prefix + "%",),
    ).fetchone()
    sequence = 1
    if row and row["last"]:
        try:
            sequence = int(str(row["last"]).rsplit("-", 1)[1]) + 1
        except (ValueError, IndexError):
            # The highest reference for today does not end in a number, so
            # there is nothing to count on from. Starting again at 1 will
            # collide with a reference that already exists and the UNIQUE
            # column will refuse the write -- which is the right outcome, but
            # the person at the counter sees only a database error. Say why.
            logs.error(
                "Reference %r does not end in a number; starting today again at 1",
                row["last"],
            )
            sequence = 1
    return f"{prefix}{sequence:04d}"


def hold(
    cart: sales_service.Cart,
    user_id: int,
    *,
    customer_id: int | None = None,
    deposit=0,
    deposit_method: str = "Cash",
    due_date: str = "",
    note: str = "",
) -> int:
    """Freeze the cart as a layaway. Returns the layaway id."""
    if cart.is_empty:
        raise LayawayError("There is nothing in the cart to set aside.")
    if any(line.is_gift_card for line in cart.lines):
        raise LayawayError("A gift card cannot be put on layaway.")
    if deposit_method not in ("Cash", "Card"):
        raise LayawayError("A deposit is taken in cash or on the card machine.")
    deposit = usd(D(deposit))
    if deposit < ZERO:
        raise LayawayError("A deposit cannot be negative.")

    _subtotal, _discount, _tax, total = cart.totals()
    if deposit > total:
        raise LayawayError(
            f"A deposit of {usd(deposit)} is more than the {usd(total)} total."
        )

    # The drawer is checked before anything is written: a layaway whose cash
    # deposit cannot be recorded must not half-exist.
    deposit_shift = None
    if deposit > ZERO and deposit_method == "Cash":
        deposit_shift = shifts_service.current_shift()
        if deposit_shift is None:
            raise LayawayError(
                "The deposit is cash, but no till shift is open. Open the till "
                "first, or take the deposit on the card machine."
            )

    with db.transaction() as conn:
        reference = next_reference(conn)
        cursor = conn.execute(
            """
            INSERT INTO layaways
                (reference, customer_id, user_id, due_date, total_usd,
                 deposit_usd, status, note)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                reference,
                customer_id if customer_id else cart.customer_id,
                user_id,
                (due_date or "").strip(),
                to_float(total),
                float(deposit),
                HELD,
                (note or cart.note or "").strip(),
            ),
        )
        layaway_id = cursor.lastrowid
        for line in cart.lines:
            conn.execute(
                """
                INSERT INTO layaway_items
                    (layaway_id, product_id, sku_at_hold, name_at_hold, qty,
                     unit_price_usd, line_total_usd)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    layaway_id,
                    line.product_id,
                    line.sku,
                    line.name,
                    line.qty,
                    to_float(line.unit_price),
                    to_float(line.line_total),
                ),
            )

    # The deposit is cash in the drawer from the moment it is taken.
    if deposit > ZERO and deposit_method == "Cash" and deposit_shift is not None:
        shifts_service.add_cash_movement(
            deposit_shift["shift_id"], user_id, config.CASH_IN, deposit,
            f"Layaway deposit {reference}",
        )

    audit.record(
        "Layaway held", "layaway", layaway_id,
        f"{reference}: {cart.item_count} item(s), {usd(total)}, deposit {usd(deposit)}",
    )
    return layaway_id


def get_layaway(layaway_id: int) -> sqlite3.Row | None:
    return db.query_one(
        """
        SELECT l.*, c.name AS customer_name, u.username AS held_by
        FROM layaways l
        LEFT JOIN customers c ON c.customer_id = l.customer_id
        LEFT JOIN users u ON u.user_id = l.user_id
        WHERE l.layaway_id = ?
        """,
        (layaway_id,),
    )


def items(layaway_id: int) -> list[sqlite3.Row]:
    return db.query(
        "SELECT * FROM layaway_items WHERE layaway_id = ? ORDER BY layaway_item_id",
        (layaway_id,),
    )


def list_layaways(
    status: str | None = None, search: str = "", limit: int = 200
) -> list[sqlite3.Row]:
    clauses, params = [], []
    if status:
        clauses.append("l.status = ?")
        params.append(status)
    if search and search.strip():
        clauses.append(
            "(l.reference LIKE ? OR c.name LIKE ? OR l.note LIKE ?)"
        )
        pattern = f"%{search.strip()}%"
        params += [pattern, pattern, pattern]
    sql = """
        SELECT l.*, c.name AS customer_name, c.phone AS customer_phone,
               u.username AS held_by,
               (SELECT COUNT(*) FROM layaway_items i WHERE i.layaway_id = l.layaway_id)
                   AS item_count
        FROM layaways l
        LEFT JOIN customers c ON c.customer_id = l.customer_id
        LEFT JOIN users u ON u.user_id = l.user_id
    """
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY l.layaway_id DESC LIMIT ?"
    params.append(limit)
    return db.query(sql, tuple(params))


def outstanding(layaway_id: int) -> float:
    row = get_layaway(layaway_id)
    if row is None:
        raise LayawayError("Layaway not found.")
    return usd(D(row["total_usd"]) - D(row["deposit_usd"]))


def collect(
    layaway_id: int,
    user_id: int,
    *,
    payment_method: str = "Cash",
    amount_paid=None,
    paid_currency: str = "USD",
) -> int:
    """Turn a held layaway into an invoice. Returns the sale id.

    The frozen lines become a cart with their held prices, and the sale runs
    through the normal checkout: the same stock check, the same invoice, the
    same receipt. What the customer owes is the total less what has already
    been deposited.
    """
    layaway = get_layaway(layaway_id)
    if layaway is None:
        raise LayawayError("Layaway not found.")
    if layaway["status"] != HELD:
        raise LayawayError(f"Layaway {layaway['reference']} is {layaway['status'].lower()}.")

    cart = sales_service.Cart()
    cart.customer_id = layaway["customer_id"]
    for item in items(layaway_id):
        product = (
            products_service.get_product(item["product_id"])
            if item["product_id"] else None
        )
        if product is None or not product["is_active"]:
            raise LayawayError(
                f"'{item['name_at_hold']}' is no longer in the catalogue. Cancel "
                "the layaway and sell what remains by hand."
            )
        line = cart.add_product(product, item["qty"])
        line.unit_price = usd(D(item["unit_price_usd"]))
        line.list_price = line.unit_price  # a frozen agreement is not an override
        line.discount = ZERO

    remainder = outstanding(layaway_id)
    paid = D(amount_paid) if amount_paid is not None else D(remainder)

    # One unit of work: the invoice, the stock it takes, and the layaway it
    # closes all commit together, so a crash halfway cannot leave the goods
    # both sold and still marked held.
    with db.transaction():
        sale_id = sales_service.create_sale(
            cart=cart,
            user_id=user_id,
            customer_id=layaway["customer_id"],
            payment_method=payment_method,
            paid_currency=paid_currency,
            amount_paid=paid,
            paid_already=layaway["deposit_usd"],
            note=f"Layaway {layaway['reference']}",
        )

        db.execute(
            "UPDATE layaways SET status = ?, completed_sale_id = ? WHERE layaway_id = ?",
            (COLLECTED, sale_id, layaway_id),
        )
    audit.record(
        "Layaway collected", "layaway", layaway_id,
        f"{layaway['reference']} became invoice #{sale_id}; {usd(remainder)} was owing",
    )
    return sale_id


def cancel(layaway_id: int, user_id: int, *, refund: bool = True) -> None:
    """Give the goods back to the shelf and, if asked, the deposit back."""
    layaway = get_layaway(layaway_id)
    if layaway is None:
        raise LayawayError("Layaway not found.")
    if layaway["status"] != HELD:
        raise LayawayError(f"Layaway {layaway['reference']} is {layaway['status'].lower()}.")

    deposit = D(layaway["deposit_usd"])
    # The drawer is checked before anything is written, so the layaway is
    # never left cancelled-with-deposit-still-owed.
    refund_shift = None
    if refund and deposit > ZERO:
        refund_shift = shifts_service.current_shift()
        if refund_shift is None:
            raise LayawayError(
                "The deposit is handed back in cash, but no till shift is open. "
                "Open the till first."
            )

    db.execute(
        "UPDATE layaways SET status = ? WHERE layaway_id = ?",
        (CANCELLED, layaway_id),
    )

    if refund and deposit > ZERO and refund_shift is not None:
        shifts_service.add_cash_movement(
            refund_shift["shift_id"], user_id, config.CASH_OUT, deposit,
            f"Layaway refund {layaway['reference']}",
        )

    audit.record(
        "Layaway cancelled", "layaway", layaway_id,
        f"{layaway['reference']}: "
        + (f"{usd(deposit)} deposit refunded" if refund and deposit > ZERO
           else "no deposit to refund"),
    )


def summary() -> dict:
    return {
        "held": db.scalar(
            "SELECT COUNT(*) FROM layaways WHERE status = ?", (HELD,), default=0
        ),
        "value_usd": float(
            db.scalar(
                "SELECT COALESCE(SUM(total_usd - deposit_usd), 0) FROM layaways "
                "WHERE status = ?",
                (HELD,),
                default=0.0,
            )
        ),
        "overdue": db.scalar(
            """
            SELECT COUNT(*) FROM layaways
            WHERE status = 'Held' AND due_date != ''
              AND date(due_date) < date('now', 'localtime')
            """,
            default=0,
        ),
    }
