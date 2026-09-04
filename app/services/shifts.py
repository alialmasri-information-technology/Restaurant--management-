"""Till shifts and cash control.

A shift is the unit a cashier is accountable for: it opens with a counted float,
collects every cash movement that happens during the day, and closes with a
physical count. The difference between what the drawer *should* hold and what it
actually holds is the variance — the number a shop owner actually looks at.

Expected cash is tracked in USD-equivalent because the drawer holds both USD and
LBP; each sale contributes at the rate that was in force when it was rung up.
"""

from __future__ import annotations

import sqlite3

from app import config, db
from app.money import ZERO, D, to_float, usd
from app.services import audit
from app.services import settings as settings_service


class ShiftError(Exception):
    """Raised for user-facing till failures."""


# Both lookups carry the operator names, so a caller never has to join again.
_SELECT = """
    SELECT s.*, o.username AS opened_by_name, c.username AS closed_by_name
    FROM shifts s
    LEFT JOIN users o ON o.user_id = s.opened_by
    LEFT JOIN users c ON c.user_id = s.closed_by
"""


def current_shift() -> sqlite3.Row | None:
    return db.query_one(
        _SELECT + " WHERE s.status = ? ORDER BY s.shift_id DESC LIMIT 1",
        (config.SHIFT_OPEN,),
    )


def get_shift(shift_id: int) -> sqlite3.Row | None:
    return db.query_one(_SELECT + " WHERE s.shift_id = ?", (shift_id,))


def shift_required() -> bool:
    return settings_service.get("require_shift", "1") == "1"


def require_open_shift() -> sqlite3.Row | None:
    """Return the open shift, or raise if the shop insists on one."""
    shift = current_shift()
    if shift is None and shift_required():
        raise ShiftError(
            "No till shift is open. Open the till (Till → Open shift) before selling."
        )
    return shift


def open_shift(user_id: int, opening_float=0, note: str = "") -> int:
    if current_shift() is not None:
        raise ShiftError("A till shift is already open. Close it before opening another.")
    opening_float = D(opening_float)
    if opening_float < ZERO:
        raise ShiftError("The opening float cannot be negative.")

    shift_id = db.execute(
        """
        INSERT INTO shifts (opened_by, opening_float_usd, exchange_rate, note)
        VALUES (?, ?, ?, ?)
        """,
        (
            user_id,
            to_float(opening_float),
            float(settings_service.exchange_rate()),
            (note or "").strip(),
        ),
    )
    audit.record("Shift opened", "shift", shift_id, f"float {usd(opening_float)}")
    return shift_id


def add_cash_movement(shift_id: int, user_id: int, kind: str, amount, reason: str = "") -> int:
    if kind not in (config.CASH_IN, config.CASH_OUT):
        raise ShiftError(f"Unknown cash movement: {kind}")
    amount = D(amount)
    if amount <= ZERO:
        raise ShiftError("Enter an amount greater than zero.")

    shift = get_shift(shift_id)
    if shift is None:
        raise ShiftError("Till shift not found.")
    if shift["status"] != config.SHIFT_OPEN:
        raise ShiftError("That till shift is already closed.")

    if kind == config.CASH_OUT:
        available = D(totals(shift_id)["expected_usd"])
        if amount > available:
            raise ShiftError(
                f"Only {usd(available)} is in the drawer; you cannot take out {usd(amount)}."
            )

    movement_id = db.execute(
        """
        INSERT INTO cash_movements (shift_id, user_id, kind, amount_usd, reason)
        VALUES (?, ?, ?, ?, ?)
        """,
        (shift_id, user_id, kind, to_float(amount), (reason or "").strip()),
    )
    audit.record(
        f"Cash {kind.lower()}", "shift", shift_id, f"{usd(amount)} — {reason or 'no reason given'}"
    )
    return movement_id


def movements(shift_id: int) -> list[sqlite3.Row]:
    return db.query(
        """
        SELECT m.*, u.username
        FROM cash_movements m
        LEFT JOIN users u ON u.user_id = m.user_id
        WHERE m.shift_id = ?
        ORDER BY m.movement_id
        """,
        (shift_id,),
    )


def totals(shift_id: int) -> dict:
    """Everything that has moved through the drawer this shift."""
    shift = db.query_one("SELECT * FROM shifts WHERE shift_id = ?", (shift_id,))
    if shift is None:
        raise ShiftError("Till shift not found.")

    sales_row = db.query_one(
        """
        SELECT COUNT(*) AS sale_count,
               COALESCE(SUM(total_usd), 0) AS sales_total,
               COALESCE(SUM(CASE WHEN payment_method = 'Cash' THEN total_usd ELSE 0 END), 0)
                   AS cash_sales,
               COALESCE(SUM(CASE WHEN payment_method != 'Cash' THEN total_usd ELSE 0 END), 0)
                   AS non_cash_sales
        FROM sales
        WHERE shift_id = ?
        """,
        (shift_id,),
    )
    returns_row = db.query_one(
        """
        SELECT COUNT(*) AS return_count,
               COALESCE(SUM(total_usd), 0) AS returns_total,
               COALESCE(SUM(CASE WHEN refund_method = 'Cash' THEN total_usd ELSE 0 END), 0)
                   AS cash_returns
        FROM returns
        WHERE shift_id = ?
        """,
        (shift_id,),
    )
    cash_row = db.query_one(
        """
        SELECT COALESCE(SUM(CASE WHEN kind = 'In' THEN amount_usd ELSE 0 END), 0)  AS cash_in,
               COALESCE(SUM(CASE WHEN kind = 'Out' THEN amount_usd ELSE 0 END), 0) AS cash_out
        FROM cash_movements
        WHERE shift_id = ?
        """,
        (shift_id,),
    )

    expected = (
        D(shift["opening_float_usd"])
        + D(sales_row["cash_sales"])
        + D(cash_row["cash_in"])
        - D(cash_row["cash_out"])
        - D(returns_row["cash_returns"])
    )

    return {
        "shift_id": shift_id,
        "opening_float": shift["opening_float_usd"],
        "sale_count": sales_row["sale_count"],
        "sales_total": sales_row["sales_total"],
        "cash_sales": sales_row["cash_sales"],
        "non_cash_sales": sales_row["non_cash_sales"],
        "return_count": returns_row["return_count"],
        "returns_total": returns_row["returns_total"],
        "cash_returns": returns_row["cash_returns"],
        "cash_in": cash_row["cash_in"],
        "cash_out": cash_row["cash_out"],
        "expected_usd": to_float(expected),
    }


def payment_mix(shift_id: int) -> list[sqlite3.Row]:
    return db.query(
        """
        SELECT payment_method, COUNT(*) AS sale_count, SUM(total_usd) AS revenue
        FROM sales
        WHERE shift_id = ?
        GROUP BY payment_method
        ORDER BY revenue DESC
        """,
        (shift_id,),
    )


def close_shift(shift_id: int, user_id: int, counted_usd=0, counted_lbp=0, note: str = "") -> dict:
    """Reconcile and close. Returns the summary, including the variance."""
    shift = db.query_one("SELECT * FROM shifts WHERE shift_id = ?", (shift_id,))
    if shift is None:
        raise ShiftError("Till shift not found.")
    if shift["status"] != config.SHIFT_OPEN:
        raise ShiftError("That till shift is already closed.")

    counted_usd = D(counted_usd)
    counted_lbp = D(counted_lbp)
    if counted_usd < ZERO or counted_lbp < ZERO:
        raise ShiftError("Counted cash cannot be negative.")

    rate = D(shift["exchange_rate"]) or settings_service.exchange_rate()
    if rate <= ZERO:
        raise ShiftError("The exchange rate for this shift is invalid.")

    summary = totals(shift_id)
    counted_total = counted_usd + (counted_lbp / rate)
    variance = usd(counted_total - D(summary["expected_usd"]))

    db.execute(
        """
        UPDATE shifts
           SET closed_by = ?, closed_at = datetime('now', 'localtime'),
               counted_usd = ?, counted_lbp = ?, expected_usd = ?, variance_usd = ?,
               status = ?, note = TRIM(note || ' ' || ?)
         WHERE shift_id = ?
        """,
        (
            user_id,
            to_float(counted_usd),
            float(counted_lbp),
            summary["expected_usd"],
            to_float(variance),
            config.SHIFT_CLOSED,
            (note or "").strip(),
            shift_id,
        ),
    )

    summary["counted_usd"] = to_float(counted_usd)
    summary["counted_lbp"] = float(counted_lbp)
    summary["counted_total_usd"] = to_float(counted_total)
    summary["variance_usd"] = to_float(variance)
    audit.record(
        "Shift closed", "shift", shift_id,
        f"expected {usd(summary['expected_usd'])}, counted {usd(counted_total)}, "
        f"variance {usd(variance)}",
    )
    return summary


def list_shifts(limit: int = 100) -> list[sqlite3.Row]:
    return db.query(
        """
        SELECT s.*, o.username AS opened_by_name, c.username AS closed_by_name,
               (SELECT COUNT(*) FROM sales sa WHERE sa.shift_id = s.shift_id) AS sale_count,
               (SELECT COALESCE(SUM(sa.total_usd), 0) FROM sales sa
                 WHERE sa.shift_id = s.shift_id) AS sales_total
        FROM shifts s
        LEFT JOIN users o ON o.user_id = s.opened_by
        LEFT JOIN users c ON c.user_id = s.closed_by
        ORDER BY s.shift_id DESC
        LIMIT ?
        """,
        (limit,),
    )
