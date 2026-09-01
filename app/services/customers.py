"""Customer records."""

from __future__ import annotations

import re
import sqlite3

from app import db

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class CustomerError(Exception):
    """Raised for user-facing customer failures."""


_SELECT = """
    SELECT c.*,
           (SELECT COUNT(*) FROM sales s
             WHERE s.customer_id = c.customer_id AND s.status = 'Completed')
               AS purchase_count,
           COALESCE((SELECT SUM(s.total_usd) FROM sales s
             WHERE s.customer_id = c.customer_id AND s.status = 'Completed'), 0)
               AS total_spent_usd,
           (SELECT MAX(s.sale_time) FROM sales s
             WHERE s.customer_id = c.customer_id) AS last_purchase
    FROM customers c
"""


def list_customers(search: str = "") -> list[sqlite3.Row]:
    sql = _SELECT
    params: tuple = ()
    if search and search.strip():
        sql += " WHERE c.name LIKE ? OR c.phone LIKE ? OR c.email LIKE ?"
        pattern = f"%{search.strip()}%"
        params = (pattern, pattern, pattern)
    sql += " ORDER BY c.name COLLATE NOCASE"
    return db.query(sql, params)


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
