"""Supplier records."""

from __future__ import annotations

import sqlite3

from app import db
from app.services import audit
from app.services.customers import EMAIL_RE


class SupplierError(Exception):
    """Raised for user-facing supplier failures."""


# The per-supplier roll-ups are one grouped pass over products and purchase
# orders, joined on — not three correlated sub-queries per supplier row.
_SELECT = """
    SELECT s.*,
           COALESCE(pc.product_count, 0) AS product_count,
           COALESCE(oc.order_count, 0) AS order_count,
           COALESCE(oc.purchased_usd, 0) AS purchased_usd
    FROM suppliers s
    LEFT JOIN (
        SELECT p.supplier_id, COUNT(*) AS product_count
        FROM products p
        WHERE p.is_active = 1
        GROUP BY p.supplier_id
    ) pc ON pc.supplier_id = s.supplier_id
    LEFT JOIN (
        SELECT o.supplier_id,
               COUNT(*) AS order_count,
               SUM(CASE WHEN o.status IN ('Received', 'Partially Received')
                        THEN o.total_cost_usd ELSE 0 END) AS purchased_usd
        FROM purchase_orders o
        GROUP BY o.supplier_id
    ) oc ON oc.supplier_id = s.supplier_id
"""


def list_suppliers(
    search: str = "", include_inactive: bool = False, limit: int | None = db.LIST_LIMIT
) -> list[sqlite3.Row]:
    clauses, params = [], []
    if not include_inactive:
        clauses.append("s.is_active = 1")
    if search and search.strip():
        clauses.append("(s.name LIKE ? OR s.contact_name LIKE ? OR s.phone LIKE ? OR s.email LIKE ?)")
        pattern = f"%{search.strip()}%"
        params += [pattern] * 4

    sql = _SELECT
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY s.name COLLATE NOCASE"
    return db.query_limited(sql, tuple(params), limit)


def get_supplier(supplier_id: int) -> sqlite3.Row | None:
    return db.query_one(_SELECT + " WHERE s.supplier_id = ?", (supplier_id,))


def find_by_name(name: str) -> sqlite3.Row | None:
    return db.query_one(
        "SELECT * FROM suppliers WHERE name = ? COLLATE NOCASE", ((name or "").strip(),)
    )


def _validate(name: str, email: str) -> None:
    if not (name or "").strip():
        raise SupplierError("Supplier name is required.")
    if email and email.strip() and not EMAIL_RE.match(email.strip()):
        raise SupplierError(f"'{email}' does not look like a valid email address.")


def create_supplier(
    *, name: str, contact_name: str = "", phone: str = "", email: str = "",
    address: str = "", payment_terms: str = "", notes: str = "",
) -> int:
    _validate(name, email)
    if find_by_name(name) is not None:
        raise SupplierError(f"A supplier named '{name.strip()}' already exists.")
    supplier_id = db.execute(
        """
        INSERT INTO suppliers
            (name, contact_name, phone, email, address, payment_terms, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            name.strip(), (contact_name or "").strip(), (phone or "").strip(),
            (email or "").strip(), (address or "").strip(),
            (payment_terms or "").strip(), (notes or "").strip(),
        ),
    )
    audit.record("Supplier created", "supplier", supplier_id, name.strip())
    return supplier_id


def update_supplier(
    supplier_id: int, *, name: str, contact_name: str = "", phone: str = "",
    email: str = "", address: str = "", payment_terms: str = "", notes: str = "",
    is_active: bool = True,
) -> None:
    _validate(name, email)
    existing = find_by_name(name)
    if existing is not None and existing["supplier_id"] != supplier_id:
        raise SupplierError(f"A supplier named '{name.strip()}' already exists.")

    before = get_supplier(supplier_id)
    db.execute(
        """
        UPDATE suppliers
           SET name = ?, contact_name = ?, phone = ?, email = ?, address = ?,
               payment_terms = ?, notes = ?, is_active = ?
         WHERE supplier_id = ?
        """,
        (
            name.strip(), (contact_name or "").strip(), (phone or "").strip(),
            (email or "").strip(), (address or "").strip(),
            (payment_terms or "").strip(), (notes or "").strip(),
            1 if is_active else 0, supplier_id,
        ),
    )
    if before is not None:
        detail = audit.describe_changes(
            dict(before),
            {"name": name.strip(), "phone": phone, "email": email,
             "payment_terms": payment_terms, "is_active": 1 if is_active else 0},
            ["name", "phone", "email", "payment_terms", "is_active"],
        )
        audit.record("Supplier updated", "supplier", supplier_id, detail or "no changes")


def delete_supplier(supplier_id: int) -> None:
    """Archive if the supplier is referenced anywhere, otherwise remove."""
    supplier = get_supplier(supplier_id)
    if supplier is None:
        raise SupplierError("Supplier not found.")
    if supplier["order_count"] or supplier["product_count"]:
        db.execute(
            "UPDATE suppliers SET is_active = 0 WHERE supplier_id = ?", (supplier_id,)
        )
        audit.record("Supplier archived", "supplier", supplier_id, supplier["name"])
        raise SupplierError(
            "This supplier is linked to products or purchase orders, so it was "
            "archived instead of deleted."
        )
    db.execute("DELETE FROM suppliers WHERE supplier_id = ?", (supplier_id,))
    audit.record("Supplier deleted", "supplier", supplier_id, supplier["name"])


def products_for(supplier_id: int) -> list[sqlite3.Row]:
    return db.query(
        """
        SELECT product_id, sku, name, stock_qty, reorder_level, cost_usd
        FROM products
        WHERE supplier_id = ?
        ORDER BY name COLLATE NOCASE
        """,
        (supplier_id,),
    )
