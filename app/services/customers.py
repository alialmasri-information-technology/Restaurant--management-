"""Customer records."""

from __future__ import annotations

import re
import sqlite3

from app import db

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
    """Past sales keep their invoice and simply revert to a walk-in sale."""
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
