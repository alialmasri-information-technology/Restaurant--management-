"""Returns and exchanges.

A return can cover part of an invoice, so the refund is worked out from the
line's own net price and then given its proportional share of any invoice-level
discount and tax. Returning two of five identical items therefore refunds
exactly two fifths of what that line contributed to the total — not the sticker
price, which would over-refund a discounted sale.
"""

from __future__ import annotations

import datetime as dt
import sqlite3

from app import config, db
from app.money import ZERO, D, to_float, usd
from app.services import accounts as accounts_service
from app.services import audit
from app.services import products as products_service
from app.services import settings as settings_service
from app.services import shifts as shifts_service


class ReturnError(Exception):
    """Raised for user-facing return failures."""


def returnable_lines(sale_id: int) -> list[sqlite3.Row]:
    """Every line of a sale with how many units are still returnable."""
    return db.query(
        """
        SELECT i.*, (i.qty - i.returned_qty) AS remaining_qty,
               (i.line_total_usd / i.qty) AS unit_net_usd
        FROM sale_items i
        WHERE i.sale_id = ?
        ORDER BY i.sale_item_id
        """,
        (sale_id,),
    )


def next_return_no(conn: sqlite3.Connection) -> str:
    today = dt.date.today().strftime("%Y%m%d")
    prefix = f"RET-{today}-"
    row = conn.execute(
        "SELECT MAX(return_no) AS last FROM returns WHERE return_no LIKE ?",
        (prefix + "%",),
    ).fetchone()
    sequence = 1
    if row and row["last"]:
        try:
            sequence = int(str(row["last"]).rsplit("-", 1)[1]) + 1
        except (ValueError, IndexError):
            sequence = 1
    return f"{prefix}{sequence:04d}"


def quote(sale_id: int, quantities: dict) -> dict:
    """Work out what a proposed return would refund, without writing anything."""
    sale = db.query_one("SELECT * FROM sales WHERE sale_id = ?", (sale_id,))
    if sale is None:
        raise ReturnError("Sale not found.")

    wanted = {int(key): int(value) for key, value in quantities.items() if int(value) > 0}
    if not wanted:
        raise ReturnError("Enter at least one quantity to return.")

    lines = {row["sale_item_id"]: row for row in returnable_lines(sale_id)}
    subtotal = D(sale["subtotal_usd"])
    if subtotal <= ZERO:
        raise ReturnError("This invoice has no value to refund.")

    # The tax rate actually charged on this invoice, recovered from its own numbers.
    taxable_base = subtotal - D(sale["discount_usd"])
    tax_rate = (D(sale["tax_usd"]) / taxable_base) if taxable_base > ZERO else ZERO

    detail = []
    line_value = ZERO
    for sale_item_id, qty in wanted.items():
        line = lines.get(sale_item_id)
        if line is None:
            raise ReturnError("One of the lines does not belong to this invoice.")
        if qty > line["remaining_qty"]:
            raise ReturnError(
                f"{line['name_at_sale']}: only {line['remaining_qty']} unit(s) "
                f"of {line['qty']} can still be returned."
            )
        unit_net = D(line["line_total_usd"]) / D(line["qty"])
        value = usd(unit_net * D(qty))
        line_value += value
        detail.append({
            "sale_item_id": sale_item_id,
            "product_id": line["product_id"],
            "name": line["name_at_sale"],
            "qty": qty,
            "unit_price_usd": to_float(unit_net),
            "line_total_usd": to_float(value),
        })

    share = line_value / subtotal
    discount_share = usd(D(sale["discount_usd"]) * share)
    net = line_value - discount_share
    tax_share = usd(net * tax_rate)
    total = usd(net + tax_share)

    return {
        "sale_id": sale_id,
        "invoice_no": sale["invoice_no"],
        "lines": detail,
        "line_value_usd": to_float(line_value),
        "discount_share_usd": to_float(discount_share),
        "tax_share_usd": to_float(tax_share),
        "total_usd": to_float(total),
    }


def create_return(
    sale_id: int, user_id: int, quantities: dict, *,
    refund_method: str = "Cash", reason: str = "", restock: bool = True,
) -> int:
    """Record a return, refund the customer, and put the stock back."""
    if refund_method not in config.PAYMENT_METHODS:
        raise ReturnError(f"Unknown refund method: {refund_method}")

    quoted = quote(sale_id, quantities)
    shift = shifts_service.current_shift()
    rate = settings_service.exchange_rate()
    sale_customer_id = db.scalar(
        "SELECT customer_id FROM sales WHERE sale_id = ?", (sale_id,)
    )
    if refund_method == "Credit" and sale_customer_id is None:
        raise ReturnError(
            "This sale has no customer, so there is no account to credit. "
            "Refund it another way."
        )

    with db.transaction() as conn:
        return_no = next_return_no(conn)
        cursor = conn.execute(
            """
            INSERT INTO returns
                (return_no, sale_id, user_id, shift_id, total_usd, exchange_rate,
                 refund_method, restock, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                return_no, sale_id, user_id,
                shift["shift_id"] if shift else None,
                quoted["total_usd"], float(rate), refund_method,
                1 if restock else 0, (reason or "").strip(),
            ),
        )
        return_id = cursor.lastrowid

        for line in quoted["lines"]:
            conn.execute(
                """
                INSERT INTO return_items
                    (return_id, sale_item_id, product_id, name_at_sale, qty,
                     unit_price_usd, line_total_usd)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    return_id, line["sale_item_id"], line["product_id"], line["name"],
                    line["qty"], line["unit_price_usd"], line["line_total_usd"],
                ),
            )
            conn.execute(
                "UPDATE sale_items SET returned_qty = returned_qty + ? WHERE sale_item_id = ?",
                (line["qty"], line["sale_item_id"]),
            )
            if restock and line["product_id"] is not None:
                products_service.adjust_stock(
                    line["product_id"], line["qty"], reason="Return", user_id=user_id,
                    note=return_no, conn=conn,
                )

        if refund_method == "Credit" and sale_customer_id is not None:
            # Goods off an account sale come back as a smaller debt, not as cash
            # out of a drawer that never took any in.
            accounts_service.credit_return(
                sale_customer_id, return_id, quoted["total_usd"],
                reference=return_no, sale_id=sale_id, user_id=user_id,
                shift_id=shift["shift_id"] if shift else None,
                conn=conn,
            )

        outstanding = conn.execute(
            """
            SELECT COALESCE(SUM(qty - returned_qty), 0) AS remaining
            FROM sale_items WHERE sale_id = ?
            """,
            (sale_id,),
        ).fetchone()["remaining"]
        if outstanding == 0:
            conn.execute(
                "UPDATE sales SET status = ? WHERE sale_id = ?",
                (config.SALE_REFUNDED, sale_id),
            )

    audit.record(
        "Return processed", "return", return_id,
        f"{return_no} against {quoted['invoice_no']}: "
        f"{sum(line['qty'] for line in quoted['lines'])} unit(s), "
        f"{usd(quoted['total_usd'])} via {refund_method}"
        + ("" if restock else ", not restocked"),
    )
    return return_id


def return_everything(sale_id: int, user_id: int, reason: str = "", restock: bool = True) -> int:
    """Convenience wrapper: refund every unit still outstanding on an invoice."""
    lines = returnable_lines(sale_id)
    quantities = {
        row["sale_item_id"]: row["remaining_qty"]
        for row in lines if row["remaining_qty"] > 0
    }
    if not quantities:
        sale = db.query_one("SELECT invoice_no FROM sales WHERE sale_id = ?", (sale_id,))
        label = sale["invoice_no"] if sale else sale_id
        raise ReturnError(f"Everything on {label} has already been returned.")
    return create_return(sale_id, user_id, quantities, reason=reason, restock=restock)


def get_return(return_id: int) -> sqlite3.Row | None:
    return db.query_one(
        """
        SELECT r.*, s.invoice_no, u.username, u.full_name AS user_name,
               c.name AS customer_name
        FROM returns r
        JOIN sales s ON s.sale_id = r.sale_id
        LEFT JOIN users u ON u.user_id = r.user_id
        LEFT JOIN customers c ON c.customer_id = s.customer_id
        WHERE r.return_id = ?
        """,
        (return_id,),
    )


def get_return_items(return_id: int) -> list[sqlite3.Row]:
    return db.query(
        "SELECT * FROM return_items WHERE return_id = ? ORDER BY return_item_id",
        (return_id,),
    )


def returns_for_sale(sale_id: int) -> list[sqlite3.Row]:
    return db.query(
        "SELECT * FROM returns WHERE sale_id = ? ORDER BY return_id", (sale_id,)
    )


def list_returns(
    date_from: str | None = None, date_to: str | None = None,
    search: str = "", limit: int = 300,
) -> list[sqlite3.Row]:
    clauses, params = db.date_range_clauses("r.created_at", date_from, date_to)
    if search and search.strip():
        clauses.append("(r.return_no LIKE ? OR s.invoice_no LIKE ? OR r.reason LIKE ?)")
        pattern = f"%{search.strip()}%"
        params += [pattern] * 3

    sql = """
        SELECT r.*, s.invoice_no, u.username,
               (SELECT COALESCE(SUM(qty), 0) FROM return_items i
                 WHERE i.return_id = r.return_id) AS unit_count
        FROM returns r
        JOIN sales s ON s.sale_id = r.sale_id
        LEFT JOIN users u ON u.user_id = r.user_id
    """
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY r.return_id DESC LIMIT ?"
    params.append(limit)
    return db.query(sql, tuple(params))
