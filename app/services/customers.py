"""Customer records."""

from __future__ import annotations

import re
import sqlite3

from app import db
from app.money import ZERO, D, fmt_usd

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_SELECT = """
    SELECT c.*,
           COALESCE(sp.purchase_count, 0) AS purchase_count,
           COALESCE(sp.total_spent_usd, 0) AS total_spent_usd,
           sp.last_purchase,
           COALESCE(led.balance_usd, 0) AS balance_usd
    FROM customers c
    LEFT JOIN (
        SELECT s.customer_id,
               COUNT(*) AS purchase_count,
               SUM(s.total_usd) AS total_spent_usd,
               MAX(s.sale_time) AS last_purchase
        FROM sales s
        WHERE s.status = 'Completed'
        GROUP BY s.customer_id
    ) sp ON sp.customer_id = c.customer_id
    LEFT JOIN (
        SELECT l.customer_id, SUM(l.amount_usd) AS balance_usd
        FROM customer_ledger l
        GROUP BY l.customer_id
    ) led ON led.customer_id = c.customer_id
"""


class CustomerError(Exception):
    """Raised for user-facing customer failures."""


def list_customers(
    search: str = "", *, owing_only: bool = False, limit: int | None = db.LIST_LIMIT
) -> list[sqlite3.Row]:
    sql = _SELECT
    clauses: list[str] = []
    params: list = []
    if search and search.strip():
        clauses.append("(c.name LIKE ? OR c.phone LIKE ? OR c.email LIKE ?)")
        pattern = f"%{search.strip()}%"
        params += [pattern, pattern, pattern]
    if owing_only:
        clauses.append("COALESCE(led.balance_usd, 0) > 0.005")
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)

    sql += " ORDER BY c.name COLLATE NOCASE"
    return db.query_limited(sql, tuple(params), limit)


def get_customer(customer_id: int) -> sqlite3.Row | None:
    return db.query_one(_SELECT + " WHERE c.customer_id = ?", (customer_id,))


def _validate(name: str, email: str) -> None:
    if not (name or "").strip():
        raise CustomerError("Customer name is required.")
    if email and email.strip() and not EMAIL_RE.match(email.strip()):
        raise CustomerError(f"'{email}' does not look like a valid email address.")


def create_customer(
    *, name: str, phone: str = "", email: str = "", address: str = "", notes: str = ""
) -> int:
    _validate(name, email)
    return db.execute(
        """
        INSERT INTO customers (name, phone, email, address, notes)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            name.strip(),
            (phone or "").strip(),
            (email or "").strip(),
            (address or "").strip(),
            (notes or "").strip(),
        ),
    )


def update_customer(
    customer_id: int,
    *,
    name: str,
    phone: str = "",
    email: str = "",
    address: str = "",
    notes: str = "",
) -> None:
    _validate(name, email)
    db.execute(
        """
        UPDATE customers
           SET name = ?, phone = ?, email = ?, address = ?, notes = ?
         WHERE customer_id = ?
        """,
        (
            name.strip(),
            (phone or "").strip(),
            (email or "").strip(),
            (address or "").strip(),
            (notes or "").strip(),
            customer_id,
        ),
    )


def delete_customer(customer_id: int) -> None:
    """Delete a customer whose account is settled and who is holding nothing.

    Past sales keep their invoice and revert to a walk-in sale, which is what
    ``ON DELETE SET NULL`` on ``sales.customer_id`` is for, and is all this
    used to say.

    The ledger is not so forgiving. ``customer_ledger.customer_id`` cascades,
    so deleting someone who owed $500 took the record of the debt with them:
    the ledger rows went, the balance stopped existing, and the day's
    receivable total dropped by $500 with nothing to show why. A shop tidying
    up its customer list would have had no way of knowing it had just written
    off what it was owed. Settle the account or adjust it to zero first, so
    that writing a debt off is a thing someone decides to do rather than a
    side effect of housekeeping.

    A layaway still held is money in the same way: goods set aside against a
    deposit already taken. Its ``customer_id`` is set to null rather than
    deleted, which leaves a parcel on a shelf with nobody's name on it.
    """
    customer = get_customer(customer_id)
    if customer is None:
        raise CustomerError("Customer not found.")

    balance = D(customer["balance_usd"])
    if balance != ZERO:
        owed = (
            f"still owes {fmt_usd(balance)}" if balance > ZERO
            else f"is owed {fmt_usd(-balance)}"
        )
        raise CustomerError(
            f"{customer['name']} {owed}. Settle the account, or adjust it to "
            f"zero if you are writing it off, before deleting the customer — "
            f"deleting them now would take the record of it with them."
        )

    held = db.scalar(
        "SELECT COUNT(*) FROM layaways WHERE customer_id = ? AND status = 'Held'",
        (customer_id,),
        default=0,
    )
    if held:
        raise CustomerError(
            f"{customer['name']} has {held} layaway(s) still held. Collect or "
            f"cancel them first, or the goods stay set aside with no name on them."
        )

    db.execute("DELETE FROM customers WHERE customer_id = ?", (customer_id,))


def purchase_history(customer_id: int, limit: int = 50) -> list[sqlite3.Row]:
    return db.query(
        """
        SELECT sale_id, invoice_no, sale_time, total_usd, status
        FROM sales
        WHERE customer_id = ?
        ORDER BY sale_id DESC
        LIMIT ?
        """,
        (customer_id, limit),
    )
