"""Purchase orders and goods receiving.

Receiving stock updates the product's cost using a weighted average of what is
already on the shelf and what just arrived, which is what makes the margin
figures in Reports honest after a price rise from the supplier.
"""

from __future__ import annotations

import datetime as dt
import sqlite3

from app import config, db
from app.money import ZERO, D, to_float, usd
from app.services import audit
from app.services import products as products_service


class PurchaseError(Exception):
    """Raised for user-facing purchasing failures."""


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #

_SELECT = """
    SELECT o.*, s.name AS supplier_name, u.username AS created_by,
           (SELECT COUNT(*) FROM purchase_order_items i WHERE i.po_id = o.po_id)
               AS line_count,
           (SELECT COALESCE(SUM(i.qty_ordered), 0) FROM purchase_order_items i
             WHERE i.po_id = o.po_id) AS units_ordered,
           (SELECT COALESCE(SUM(i.qty_received), 0) FROM purchase_order_items i
             WHERE i.po_id = o.po_id) AS units_received
    FROM purchase_orders o
    LEFT JOIN suppliers s ON s.supplier_id = o.supplier_id
    LEFT JOIN users u ON u.user_id = o.user_id
"""


def get_po(po_id: int) -> sqlite3.Row | None:
    return db.query_one(_SELECT + " WHERE o.po_id = ?", (po_id,))


def po_items(po_id: int) -> list[sqlite3.Row]:
    return db.query(
        """
        SELECT i.*, p.sku, p.name, p.stock_qty, p.cost_usd AS current_cost
        FROM purchase_order_items i
        JOIN products p ON p.product_id = i.product_id
        WHERE i.po_id = ?
        ORDER BY i.po_item_id
        """,
        (po_id,),
    )


def list_pos(
    search: str = "", status: str | None = None, supplier_id: int | None = None,
    limit: int = 300,
) -> list[sqlite3.Row]:
    clauses, params = [], []
    if status and status != "All":
        clauses.append("o.status = ?")
        params.append(status)
    if supplier_id:
        clauses.append("o.supplier_id = ?")
        params.append(supplier_id)
    if search and search.strip():
        clauses.append("(o.po_no LIKE ? OR s.name LIKE ? OR o.note LIKE ?)")
        pattern = f"%{search.strip()}%"
        params += [pattern] * 3

    sql = _SELECT
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY o.po_id DESC LIMIT ?"
    params.append(limit)
    return db.query(sql, tuple(params))


def next_po_no(conn: sqlite3.Connection) -> str:
    today = dt.date.today().strftime("%Y%m%d")
    prefix = f"PO-{today}-"
    row = conn.execute(
        "SELECT MAX(po_no) AS last FROM purchase_orders WHERE po_no LIKE ?",
        (prefix + "%",),
    ).fetchone()
    sequence = 1
    if row and row["last"]:
        try:
            sequence = int(str(row["last"]).rsplit("-", 1)[1]) + 1
        except (ValueError, IndexError):
            sequence = 1
    return f"{prefix}{sequence:04d}"


# --------------------------------------------------------------------------- #
# Writes
# --------------------------------------------------------------------------- #

def _normalise_lines(lines) -> list[tuple[int, int, D]]:
    """Validate and merge the (product_id, qty, unit_cost) triples."""
    merged: dict[int, list] = {}
    for line in lines:
        if isinstance(line, dict):
            product_id = int(line["product_id"])
            qty = int(line["qty"])
            unit_cost = D(line.get("unit_cost", 0))
        else:
            product_id, qty, unit_cost = int(line[0]), int(line[1]), D(line[2])
        if qty <= 0:
            raise PurchaseError("Every line needs a quantity of at least 1.")
        if unit_cost < ZERO:
            raise PurchaseError("Unit cost cannot be negative.")
        if product_id in merged:
            merged[product_id][0] += qty
            merged[product_id][1] = unit_cost
        else:
            merged[product_id] = [qty, unit_cost]

    if not merged:
        raise PurchaseError("Add at least one product to the purchase order.")
    return [(pid, values[0], values[1]) for pid, values in merged.items()]


def create_po(
    *, supplier_id: int | None, user_id: int, lines, expected_date: str = "",
    note: str = "", status: str = config.PO_DRAFT,
) -> int:
    if status not in (config.PO_DRAFT, config.PO_ORDERED):
        raise PurchaseError("A new purchase order must be a draft or an order.")
    normalised = _normalise_lines(lines)

    with db.transaction() as conn:
        po_no = next_po_no(conn)
        total = ZERO
        cursor = conn.execute(
            """
            INSERT INTO purchase_orders
                (po_no, supplier_id, user_id, expected_date, status, total_cost_usd, note)
            VALUES (?, ?, ?, ?, ?, 0, ?)
            """,
            (po_no, supplier_id, user_id, (expected_date or "").strip(), status,
             (note or "").strip()),
        )
        po_id = cursor.lastrowid

        for product_id, qty, unit_cost in normalised:
            product = conn.execute(
                "SELECT name FROM products WHERE product_id = ?", (product_id,)
            ).fetchone()
            if product is None:
                raise PurchaseError("One of the products no longer exists.")
            line_total = usd(D(qty) * unit_cost)
            total += line_total
            conn.execute(
                """
                INSERT INTO purchase_order_items
                    (po_id, product_id, qty_ordered, unit_cost_usd, line_total_usd)
                VALUES (?, ?, ?, ?, ?)
                """,
                (po_id, product_id, qty, to_float(unit_cost), to_float(line_total)),
            )

        conn.execute(
            "UPDATE purchase_orders SET total_cost_usd = ? WHERE po_id = ?",
            (to_float(total), po_id),
        )

    audit.record("Purchase order created", "purchase_order", po_id,
                 f"{po_no}, {len(normalised)} lines, {usd(total)}")
    return po_id


def update_po(po_id: int, *, supplier_id: int | None, lines, expected_date: str = "",
              note: str = "") -> None:
    """Replace the lines of a PO that has not been received against yet."""
    po = get_po(po_id)
    if po is None:
        raise PurchaseError("Purchase order not found.")
    if po["status"] in (config.PO_RECEIVED, config.PO_PARTIAL, config.PO_CANCELLED):
        raise PurchaseError(
            f"{po['po_no']} is {po['status'].lower()} and can no longer be edited."
        )
    normalised = _normalise_lines(lines)

    with db.transaction() as conn:
        conn.execute("DELETE FROM purchase_order_items WHERE po_id = ?", (po_id,))
        total = ZERO
        for product_id, qty, unit_cost in normalised:
            line_total = usd(D(qty) * unit_cost)
            total += line_total
            conn.execute(
                """
                INSERT INTO purchase_order_items
                    (po_id, product_id, qty_ordered, unit_cost_usd, line_total_usd)
                VALUES (?, ?, ?, ?, ?)
                """,
                (po_id, product_id, qty, to_float(unit_cost), to_float(line_total)),
            )
        conn.execute(
            """
            UPDATE purchase_orders
               SET supplier_id = ?, expected_date = ?, note = ?, total_cost_usd = ?
             WHERE po_id = ?
            """,
            (supplier_id, (expected_date or "").strip(), (note or "").strip(),
             to_float(total), po_id),
        )
    audit.record("Purchase order updated", "purchase_order", po_id, po["po_no"])


def set_status(po_id: int, status: str) -> None:
    po = get_po(po_id)
    if po is None:
        raise PurchaseError("Purchase order not found.")
    if status not in config.PO_STATUSES:
        raise PurchaseError(f"Unknown status: {status}")
    if po["status"] == config.PO_RECEIVED and status != config.PO_RECEIVED:
        raise PurchaseError("A fully received order cannot be reopened.")
    db.execute("UPDATE purchase_orders SET status = ? WHERE po_id = ?", (status, po_id))
    audit.record("Purchase order " + status.lower(), "purchase_order", po_id, po["po_no"])


def receive(po_id: int, user_id: int, quantities: dict, update_cost: bool = True) -> dict:
    """Book in stock against a purchase order.

    ``quantities`` maps ``po_item_id`` to the number of units arriving now.
    Returns a summary of what was received.
    """
    po = get_po(po_id)
    if po is None:
        raise PurchaseError("Purchase order not found.")
    if po["status"] == config.PO_CANCELLED:
        raise PurchaseError(f"{po['po_no']} was cancelled.")
    if po["status"] == config.PO_RECEIVED:
        raise PurchaseError(f"{po['po_no']} has already been fully received.")

    wanted = {int(key): int(value) for key, value in quantities.items() if int(value) != 0}
    if not wanted:
        raise PurchaseError("Enter at least one quantity to receive.")

    received_units = 0
    received_value = ZERO

    with db.transaction() as conn:
        for po_item_id, qty in wanted.items():
            item = conn.execute(
                """
                SELECT i.*, p.name, p.stock_qty, p.cost_usd
                FROM purchase_order_items i
                JOIN products p ON p.product_id = i.product_id
                WHERE i.po_item_id = ? AND i.po_id = ?
                """,
                (po_item_id, po_id),
            ).fetchone()
            if item is None:
                raise PurchaseError("One of the lines does not belong to this order.")
            if qty < 0:
                raise PurchaseError("Received quantity cannot be negative.")
            outstanding = item["qty_ordered"] - item["qty_received"]
            if qty > outstanding:
                raise PurchaseError(
                    f"{item['name']}: only {outstanding} unit(s) are still outstanding."
                )

            unit_cost = D(item["unit_cost_usd"])
            if update_cost:
                new_cost = _weighted_average_cost(
                    D(item["stock_qty"]), D(item["cost_usd"]), D(qty), unit_cost
                )
                conn.execute(
                    "UPDATE products SET cost_usd = ? WHERE product_id = ?",
                    (to_float(new_cost), item["product_id"]),
                )

            products_service.adjust_stock(
                item["product_id"], qty, reason="Purchase", user_id=user_id,
                note=po["po_no"], conn=conn,
            )
            conn.execute(
                "UPDATE purchase_order_items SET qty_received = qty_received + ? WHERE po_item_id = ?",
                (qty, po_item_id),
            )
            received_units += qty
            received_value += usd(D(qty) * unit_cost)

        remaining = conn.execute(
            """
            SELECT COALESCE(SUM(qty_ordered - qty_received), 0) AS outstanding
            FROM purchase_order_items WHERE po_id = ?
            """,
            (po_id,),
        ).fetchone()["outstanding"]
        status = config.PO_RECEIVED if remaining == 0 else config.PO_PARTIAL
        conn.execute(
            """
            UPDATE purchase_orders
               SET status = ?, received_at = datetime('now', 'localtime')
             WHERE po_id = ?
            """,
            (status, po_id),
        )

    audit.record(
        "Stock received", "purchase_order", po_id,
        f"{po['po_no']}: {received_units} units worth {usd(received_value)} → {status}",
    )
    return {
        "po_no": po["po_no"],
        "units": received_units,
        "value_usd": to_float(received_value),
        "status": status,
    }


def _weighted_average_cost(stock_qty, old_cost, received_qty, unit_cost):
    """New unit cost after mixing existing stock with an incoming delivery."""
    stock_qty = max(D(stock_qty), ZERO)
    total_units = stock_qty + D(received_qty)
    if total_units <= ZERO:
        return usd(unit_cost)
    return usd(((stock_qty * D(old_cost)) + (D(received_qty) * D(unit_cost))) / total_units)


def suggested_reorder() -> list[sqlite3.Row]:
    """Active products at or below their reorder level, with their supplier."""
    return db.query(
        """
        SELECT p.product_id, p.sku, p.name, p.stock_qty, p.reorder_level, p.cost_usd,
               p.supplier_id, s.name AS supplier_name,
               MAX(p.reorder_level * 2 - p.stock_qty, 1) AS suggested_qty
        FROM products p
        LEFT JOIN suppliers s ON s.supplier_id = p.supplier_id
        WHERE p.is_active = 1 AND p.stock_qty <= p.reorder_level
        ORDER BY s.name COLLATE NOCASE, p.name COLLATE NOCASE
        """
    )
