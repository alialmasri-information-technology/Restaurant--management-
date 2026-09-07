"""Stock takes: counting the shelves and reconciling them against the system.

A shop that never counts drifts. Theft, breakage, miskeyed receiving and
mis-scanned sales all leave the recorded stock higher than what is on the shelf,
and the only way to find out is to count.

Three decisions shape this module:

* **Expected quantities are frozen when the count opens.** A count of a busy
  shop takes an hour, and the till keeps ringing during it. Comparing a counted
  figure against a stock level that moved underneath the counter would report
  every sale as shrinkage.

* **Applying posts the *difference*, not the count.** The adjustment written to
  stock is ``counted - expected``, so any selling that happened during the count
  survives it. Writing the counted number in directly would silently undo it.

* **Nothing touches stock until the count is applied.** An open count is a
  worksheet. It can be abandoned with no trace on the shelves, and a variance
  can be reviewed and signed off before it becomes real.
"""

from __future__ import annotations

import datetime as dt
import sqlite3

from app import config, db
from app.money import ZERO, D, usd
from app.services import audit
from app.services import products as products_service


class StockTakeError(Exception):
    """Raised for user-facing stock take failures."""


_SELECT = """
    SELECT t.*, c.name AS category_name,
           o.username AS opened_by_name, x.username AS closed_by_name,
           (SELECT COUNT(*) FROM stock_take_items i
             WHERE i.stock_take_id = t.stock_take_id) AS line_count,
           (SELECT COUNT(*) FROM stock_take_items i
             WHERE i.stock_take_id = t.stock_take_id AND i.counted_qty IS NOT NULL)
               AS counted_count
    FROM stock_takes t
    LEFT JOIN categories c ON c.category_id = t.category_id
    LEFT JOIN users o ON o.user_id = t.opened_by
    LEFT JOIN users x ON x.user_id = t.closed_by
"""


# --------------------------------------------------------------------------- #
# Opening a count
# --------------------------------------------------------------------------- #

def next_reference(conn: sqlite3.Connection) -> str:
    """``CNT-YYYYMMDD-01``, sequential within the day.

    Derived from the highest existing reference rather than a row count, so a
    cancelled count never hands its number to the next one.
    """
    today = dt.date.today().strftime("%Y%m%d")
    prefix = f"CNT-{today}-"
    row = conn.execute(
        "SELECT MAX(reference) AS last FROM stock_takes WHERE reference LIKE ?",
        (prefix + "%",),
    ).fetchone()
    sequence = 1
    if row and row["last"]:
        try:
            sequence = int(str(row["last"]).rsplit("-", 1)[1]) + 1
        except (ValueError, IndexError):
            sequence = 1
    return f"{prefix}{sequence:02d}"


def current_count() -> sqlite3.Row | None:
    """The open count, if there is one. Only one may be open at a time."""
    return db.query_one(
        _SELECT + " WHERE t.status = ? ORDER BY t.stock_take_id DESC LIMIT 1",
        (config.TAKE_OPEN,),
    )


def open_count(
    user_id: int,
    *,
    category_id: int | None = None,
    include_zero_stock: bool = False,
    note: str = "",
) -> int:
    """Freeze a worksheet of everything in scope. Returns the new count's id.

    ``include_zero_stock`` decides whether products the system believes are out
    of stock are on the sheet. They usually should be for a full count — that is
    exactly where a forgotten box turns up — but not for a spot check.
    """
    if current_count() is not None:
        raise StockTakeError(
            "A stock take is already open. Apply or cancel it before starting another."
        )

    clauses = ["p.is_active = 1"]
    params: list = []
    if category_id:
        clauses.append("p.category_id = ?")
        params.append(category_id)
    if not include_zero_stock:
        clauses.append("p.stock_qty != 0")

    products = db.query(
        "SELECT p.product_id, p.sku, p.name, p.stock_qty, p.cost_usd FROM products p "
        "WHERE " + " AND ".join(clauses) + " ORDER BY p.name COLLATE NOCASE",
        tuple(params),
    )
    if not products:
        raise StockTakeError("No products match that scope, so there is nothing to count.")

    scope = "All products"
    if category_id:
        name = db.scalar(
            "SELECT name FROM categories WHERE category_id = ?", (category_id,)
        )
        scope = f"Category: {name}" if name else "Category"

    with db.transaction() as conn:
        reference = next_reference(conn)
        cursor = conn.execute(
            """
            INSERT INTO stock_takes (reference, opened_by, status, scope, category_id, note)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (reference, user_id, config.TAKE_OPEN, scope, category_id, (note or "").strip()),
        )
        stock_take_id = cursor.lastrowid
        conn.executemany(
            """
            INSERT INTO stock_take_items
                (stock_take_id, product_id, sku_at_count, name_at_count,
                 expected_qty, cost_usd)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    stock_take_id,
                    row["product_id"],
                    row["sku"],
                    row["name"],
                    row["stock_qty"],
                    row["cost_usd"],
                )
                for row in products
            ],
        )

    audit.record(
        "Stock take opened", "stock_take", stock_take_id,
        f"{reference}: {len(products)} line(s), {scope}",
    )
    return stock_take_id


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #

def get_count(stock_take_id: int) -> sqlite3.Row | None:
    return db.query_one(_SELECT + " WHERE t.stock_take_id = ?", (stock_take_id,))


def list_counts(limit: int = 50) -> list[sqlite3.Row]:
    return db.query(
        _SELECT + " ORDER BY t.stock_take_id DESC LIMIT ?", (limit,)
    )


def list_items(
    stock_take_id: int,
    search: str = "",
    *,
    only_uncounted: bool = False,
    only_variances: bool = False,
) -> list[sqlite3.Row]:
    """Worksheet lines, with the variance already computed for display."""
    clauses = ["i.stock_take_id = ?"]
    params: list = [stock_take_id]

    if search and search.strip():
        clauses.append("(i.name_at_count LIKE ? OR i.sku_at_count LIKE ?)")
        pattern = f"%{search.strip()}%"
        params += [pattern, pattern]
    if only_uncounted:
        clauses.append("i.counted_qty IS NULL")
    if only_variances:
        clauses.append("i.counted_qty IS NOT NULL AND i.counted_qty != i.expected_qty")

    return db.query(
        f"""
        SELECT i.*,
               (i.counted_qty - i.expected_qty) AS variance_qty,
               ((i.counted_qty - i.expected_qty) * i.cost_usd) AS variance_usd,
               p.barcode
        FROM stock_take_items i
        LEFT JOIN products p ON p.product_id = i.product_id
        WHERE {" AND ".join(clauses)}
        ORDER BY i.name_at_count COLLATE NOCASE
        """,
        tuple(params),
    )


def summarise(rows) -> dict:
    """The same figures as :func:`summary`, from worksheet rows already in hand.

    The counting screen holds the whole sheet in memory and patches it as each
    barcode arrives. Asking the database to re-total a thousand lines after
    every scan is a round trip to learn what the screen already knows, so the
    totals are added up here instead. :func:`summary` remains the answer for
    anyone who has the count's id and not its lines; a test holds the two to
    the same result.
    """
    data = {
        "line_count": 0,
        "counted_lines": 0,
        "net_units": 0,
        "surplus_units": 0,
        "shortage_units": 0,
        "variance_lines": 0,
        "net_value": 0.0,
    }
    for row in rows:
        data["line_count"] += 1
        counted = row["counted_qty"]
        if counted is None:
            continue
        expected = row["expected_qty"]
        data["counted_lines"] += 1
        difference = counted - expected
        data["net_units"] += difference
        if difference > 0:
            data["surplus_units"] += difference
        elif difference < 0:
            data["shortage_units"] += -difference
        if difference:
            data["variance_lines"] += 1
        data["net_value"] += difference * row["cost_usd"]
    data["uncounted_lines"] = data["line_count"] - data["counted_lines"]
    return data


def summary(stock_take_id: int) -> dict:
    """Progress and variance for one count, in units and at cost."""
    row = db.query_one(
        """
        SELECT COUNT(*) AS line_count,
               COALESCE(SUM(counted_qty IS NOT NULL), 0) AS counted_lines,
               COALESCE(SUM(CASE WHEN counted_qty IS NOT NULL
                                 THEN counted_qty - expected_qty ELSE 0 END), 0)
                   AS net_units,
               COALESCE(SUM(CASE WHEN counted_qty > expected_qty
                                 THEN counted_qty - expected_qty ELSE 0 END), 0)
                   AS surplus_units,
               COALESCE(SUM(CASE WHEN counted_qty < expected_qty
                                 THEN expected_qty - counted_qty ELSE 0 END), 0)
                   AS shortage_units,
               COALESCE(SUM(CASE WHEN counted_qty IS NOT NULL
                                 AND counted_qty != expected_qty THEN 1 ELSE 0 END), 0)
                   AS variance_lines,
               COALESCE(SUM(CASE WHEN counted_qty IS NOT NULL
                                 THEN (counted_qty - expected_qty) * cost_usd
                                 ELSE 0 END), 0) AS net_value
        FROM stock_take_items
        WHERE stock_take_id = ?
        """,
        (stock_take_id,),
    )
    data = dict(row) if row else {}
    data["uncounted_lines"] = (data.get("line_count", 0) or 0) - (
        data.get("counted_lines", 0) or 0
    )
    return data


# --------------------------------------------------------------------------- #
# Counting
# --------------------------------------------------------------------------- #

def _open_or_raise(stock_take_id: int) -> sqlite3.Row:
    count = get_count(stock_take_id)
    if count is None:
        raise StockTakeError("That stock take no longer exists.")
    if count["status"] != config.TAKE_OPEN:
        raise StockTakeError(
            f"Stock take {count['reference']} is {count['status'].lower()} and can no "
            "longer be edited."
        )
    return count


def record_count(stock_take_id: int, product_id: int, counted_qty) -> int:
    """Write a counted quantity onto one line. Returns the quantity stored."""
    _open_or_raise(stock_take_id)
    try:
        counted_qty = int(counted_qty)
    except (TypeError, ValueError) as exc:
        raise StockTakeError("A counted quantity must be a whole number.") from exc
    if counted_qty < 0:
        raise StockTakeError("A counted quantity cannot be negative.")

    with db.transaction() as conn:
        cursor = conn.execute(
            """
            UPDATE stock_take_items
               SET counted_qty = ?, counted_at = datetime('now', 'localtime')
             WHERE stock_take_id = ? AND product_id = ?
            """,
            (counted_qty, stock_take_id, product_id),
        )
        if cursor.rowcount == 0:
            raise StockTakeError("That product is not on this count sheet.")
    return counted_qty


def add_to_count(stock_take_id: int, product_id: int, delta: int = 1) -> int:
    """Increase a line's counted quantity — the scan-as-you-go path.

    A line that has not been counted yet starts from zero rather than from the
    expected quantity, so scanning three of something records three, not
    "expected plus three".
    """
    _open_or_raise(stock_take_id)
    row = db.query_one(
        "SELECT counted_qty FROM stock_take_items WHERE stock_take_id = ? AND product_id = ?",
        (stock_take_id, product_id),
    )
    if row is None:
        raise StockTakeError("That product is not on this count sheet.")
    current = row["counted_qty"] or 0
    return record_count(stock_take_id, product_id, max(0, current + int(delta)))


def scan(stock_take_id: int, code: str, qty: int = 1) -> sqlite3.Row:
    """Count one scanned barcode or SKU. Returns the product that was counted."""
    product = products_service.get_by_code(code)
    if product is None:
        raise StockTakeError(f"No product matches '{code}'.")
    add_to_count(stock_take_id, product["product_id"], qty)
    return product


def clear_line(stock_take_id: int, product_id: int) -> None:
    """Put a line back to uncounted — the counter miskeyed and wants to redo it."""
    _open_or_raise(stock_take_id)
    db.execute(
        """
        UPDATE stock_take_items SET counted_qty = NULL, counted_at = NULL
         WHERE stock_take_id = ? AND product_id = ?
        """,
        (stock_take_id, product_id),
    )


def count_remaining_as_expected(stock_take_id: int) -> int:
    """Accept the system figure for every line still uncounted.

    The honest way to finish a partial count: lines nobody looked at are
    recorded as agreeing, rather than being silently treated as zero.
    """
    _open_or_raise(stock_take_id)
    with db.transaction() as conn:
        cursor = conn.execute(
            """
            UPDATE stock_take_items
               SET counted_qty = expected_qty,
                   counted_at = datetime('now', 'localtime')
             WHERE stock_take_id = ? AND counted_qty IS NULL
            """,
            (stock_take_id,),
        )
        return cursor.rowcount


# --------------------------------------------------------------------------- #
# Closing
# --------------------------------------------------------------------------- #

def apply_count(stock_take_id: int, user_id: int, note: str = "") -> dict:
    """Post every variance to stock and close the count.

    Lines that were never counted are left alone — an uncounted shelf is not
    evidence of an empty one. Returns the summary as it stood when applied.
    """
    count = _open_or_raise(stock_take_id)
    totals = summary(stock_take_id)
    if not totals.get("counted_lines"):
        raise StockTakeError("Nothing has been counted yet, so there is nothing to apply.")

    variances = db.query(
        """
        SELECT product_id, name_at_count, expected_qty, counted_qty, cost_usd
        FROM stock_take_items
        WHERE stock_take_id = ? AND counted_qty IS NOT NULL
          AND counted_qty != expected_qty
        """,
        (stock_take_id,),
    )

    applied = 0
    clamped: list[str] = []
    with db.transaction() as conn:
        for line in variances:
            change = int(line["counted_qty"]) - int(line["expected_qty"])
            stock_now = conn.execute(
                "SELECT stock_qty FROM products WHERE product_id = ?",
                (line["product_id"],),
            ).fetchone()
            if stock_now is None:
                continue  # deleted mid-count; the worksheet keeps the history
            # Selling during the count has already moved stock down. Posting the
            # difference preserves those sales — but it can still overshoot zero
            # if more was sold than the counter found, so clamp and say so.
            if stock_now["stock_qty"] + change < 0:
                clamped.append(line["name_at_count"])
                change = -stock_now["stock_qty"]
            if change == 0:
                continue
            products_service.adjust_stock(
                line["product_id"],
                change,
                reason="Stock take",
                user_id=user_id,
                note=f"{count['reference']} counted {line['counted_qty']}",
                conn=conn,
            )
            applied += 1

        conn.execute(
            """
            UPDATE stock_takes
               SET status = ?, closed_by = ?, closed_at = datetime('now', 'localtime'),
                   note = CASE WHEN ? != '' THEN ? ELSE note END
             WHERE stock_take_id = ?
            """,
            (config.TAKE_APPLIED, user_id, (note or "").strip(),
             (note or "").strip(), stock_take_id),
        )

    detail = (
        f"{count['reference']}: {applied} adjustment(s), "
        f"{totals['net_units']:+d} units, {usd(totals['net_value'])} at cost"
    )
    if clamped:
        detail += f" (clamped at zero: {', '.join(clamped[:5])})"
    audit.record("Stock take applied", "stock_take", stock_take_id, detail)

    totals["adjustments"] = applied
    totals["clamped"] = clamped
    return totals


def cancel_count(stock_take_id: int, user_id: int, reason: str = "") -> None:
    """Abandon a count. Stock is untouched; the worksheet is kept for the record."""
    count = _open_or_raise(stock_take_id)
    db.execute(
        """
        UPDATE stock_takes
           SET status = ?, closed_by = ?, closed_at = datetime('now', 'localtime')
         WHERE stock_take_id = ?
        """,
        (config.TAKE_CANCELLED, user_id, stock_take_id),
    )
    audit.record(
        "Stock take cancelled", "stock_take", stock_take_id,
        f"{count['reference']}" + (f": {reason.strip()}" if reason.strip() else ""),
    )


def variance_value(stock_take_id: int):
    """Net variance at cost, as a Decimal. Negative means stock went missing."""
    total = db.scalar(
        """
        SELECT COALESCE(SUM((counted_qty - expected_qty) * cost_usd), 0)
        FROM stock_take_items
        WHERE stock_take_id = ? AND counted_qty IS NOT NULL
        """,
        (stock_take_id,),
        default=0.0,
    )
    return usd(D(total or 0)) if total else ZERO
