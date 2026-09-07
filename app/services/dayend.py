"""The end of the trading day, on one sheet.

Everything needed to close up already existed, but it was scattered: the Z
report knew about one drawer, Reports knew about revenue and margin, Customers
knew what was owed, and nothing tied a day together. Somebody closing the shop
had to open four screens and add up by hand — which is exactly when a number
gets missed.

This module composes the day. It owns no tables and no rules of its own; it
reads what the other services already record and arranges it in the order the
person locking the door works through:

1. **Did we trade well?** sales, returns, what it cost, what was left.
2. **Does the money add up?** every drawer opened that day, expected against
   counted, with the day's total variance underneath.
3. **What is still outstanding?** put on account today, paid off today, and the
   receivable the shop is carrying into tomorrow.
4. **What moved that was not a sale?** stock received, stock takes posted.

The day is bounded by the *date*, not by a shift. A shift left open overnight
still belongs to the day it opened, and the report says so rather than quietly
excluding it — an unclosed drawer is the single most common reason a day does
not reconcile, so it is called out at the top instead of being discovered a
week later.
"""

from __future__ import annotations

import datetime as dt

from app import db
from app.money import ZERO, D, to_float, usd
from app.services import reports as reports_service
from app.services import shifts as shifts_service


def today() -> str:
    return dt.date.today().isoformat()


def _date(value) -> str:
    """Accept a date, a datetime or an ISO string; always return ISO."""
    if value is None:
        return today()
    if isinstance(value, dt.datetime):
        return value.date().isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    return str(value)[:10]


# --------------------------------------------------------------------------- #
# The pieces
# --------------------------------------------------------------------------- #

def shifts_for(date: str) -> list[dict]:
    """Every drawer opened on ``date``, with its reconciliation."""
    rows = db.query(
        """
        SELECT s.*, o.username AS opened_by_name, c.username AS closed_by_name
        FROM shifts s
        LEFT JOIN users o ON o.user_id = s.opened_by
        LEFT JOIN users c ON c.user_id = s.closed_by
        WHERE s.opened_at >= date(?) AND s.opened_at < date(?, '+1 day')
        ORDER BY s.shift_id
        """,
        (date, date),
    )
    out = []
    for row in rows:
        totals = shifts_service.totals(row["shift_id"])
        entry = dict(row)
        entry["totals"] = totals
        entry["is_open"] = not row["closed_at"]
        # An open drawer has nothing counted, so it has no variance yet — not a
        # variance of zero, which would wrongly read as "balanced".
        entry["variance_usd"] = None if entry["is_open"] else row["variance_usd"]
        out.append(entry)
    return out


def account_movement(date: str) -> dict:
    """What went on account and what came off it during ``date``."""
    row = db.query_one(
        """
        SELECT COALESCE(SUM(CASE WHEN kind = 'Sale' THEN amount_usd ELSE 0 END), 0)
                   AS charged,
               COALESCE(SUM(CASE WHEN kind = 'Payment' THEN -amount_usd ELSE 0 END), 0)
                   AS collected,
               COALESCE(SUM(CASE WHEN kind = 'Payment' AND method = 'Cash'
                                 THEN -amount_usd ELSE 0 END), 0)
                   AS collected_cash,
               COALESCE(SUM(CASE WHEN kind = 'Refund' THEN -amount_usd ELSE 0 END), 0)
                   AS credited,
               COALESCE(SUM(CASE WHEN kind = 'Adjustment' THEN amount_usd ELSE 0 END), 0)
                   AS adjusted
        FROM customer_ledger
        WHERE at >= date(?) AND at < date(?, '+1 day')
        """,
        (date, date),
    )
    movement = dict(row) if row else {}
    # The receivable as it stood at the end of that day, which is not today's
    # figure once the report is reprinted a week later.
    movement["closing_receivable"] = db.scalar(
        """
        SELECT COALESCE(SUM(balance), 0) FROM (
            SELECT SUM(amount_usd) AS balance
            FROM customer_ledger
            WHERE at < date(?, '+1 day')
            GROUP BY customer_id
            HAVING SUM(amount_usd) > 0.005
        )
        """,
        (date,),
        default=0.0,
    )
    return movement


def stock_takes_for(date: str) -> list[dict]:
    """Counts posted on ``date``, with what the variance was worth at cost."""
    rows = db.query(
        """
        SELECT t.*, u.username AS applied_by_name
        FROM stock_takes t
        LEFT JOIN users u ON u.user_id = t.closed_by
        WHERE t.status = 'Applied' AND t.closed_at >= date(?) AND t.closed_at < date(?, '+1 day')
        ORDER BY t.stock_take_id
        """,
        (date, date),
    )
    out = []
    for row in rows:
        entry = dict(row)
        value = db.query_one(
            """
            SELECT COALESCE(SUM(CASE WHEN i.counted_qty IS NOT NULL
                                     THEN i.counted_qty - i.expected_qty ELSE 0 END), 0)
                       AS units,
                   COALESCE(SUM(CASE WHEN i.counted_qty IS NOT NULL
                                     THEN (i.counted_qty - i.expected_qty) * i.cost_usd
                                     ELSE 0 END), 0) AS value_usd
            FROM stock_take_items i
            WHERE i.stock_take_id = ?
            """,
            (row["stock_take_id"],),
        )
        entry["variance_units"] = value["units"] if value else 0
        entry["variance_usd"] = value["value_usd"] if value else 0.0
        out.append(entry)
    return out


# --------------------------------------------------------------------------- #
# The whole day
# --------------------------------------------------------------------------- #

def day_summary(date=None) -> dict:
    """Everything the end-of-day sheet prints, for one date."""
    date = _date(date)

    trading = reports_service.summary(date, date)
    drawers = shifts_for(date)
    account = account_movement(date)

    open_drawers = [s for s in drawers if s["is_open"]]
    expected = sum(D(s["totals"]["expected_usd"]) for s in drawers)
    counted = sum(D(s["counted_usd"]) for s in drawers if not s["is_open"])
    variance = sum(D(s["variance_usd"]) for s in drawers if not s["is_open"])
    # Expected covers every drawer; counted and variance can only cover the ones
    # that were counted. Printed as a column those three numbers contradict each
    # other the moment a till is left open -- 300 expected, 150 counted, and a
    # variance of zero underneath calling itself BALANCED. So the figure that
    # pairs with counted is kept separately, and the money still sitting in an
    # open drawer is named rather than left to look like a shortfall.
    expected_counted = sum(
        D(s["totals"]["expected_usd"]) for s in drawers if not s["is_open"]
    )
    expected_open = sum(D(s["totals"]["expected_usd"]) for s in open_drawers)

    revenue = D(trading["revenue"])
    profit = D(trading["gross_profit"])

    return {
        "date": date,
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        # 1. trading
        "sale_count": trading["sale_count"],
        "gross_revenue": trading["gross_revenue"],
        "discounts": trading["discounts"],
        "refund_count": trading["refund_count"],
        "refund_total": trading["refund_total"],
        "revenue": trading["revenue"],
        "tax": trading["tax"],
        "units": trading["units"],
        "gross_profit": trading["gross_profit"],
        "margin": to_float(profit / revenue * 100) if revenue else 0.0,
        "average_sale": trading["average_sale"],
        "payment_mix": [dict(row) for row in reports_service.payment_breakdown(date, date)],
        "by_user": [dict(row) for row in reports_service.sales_by_user(date, date)],
        "top_products": [
            dict(row) for row in reports_service.top_products(date, date, limit=5)
        ],
        # 2. the money
        "shifts": drawers,
        "shift_count": len(drawers),
        "open_shifts": [s["shift_id"] for s in open_drawers],
        "expected_usd": to_float(expected),
        "expected_counted_usd": to_float(expected_counted),
        "expected_open_usd": to_float(expected_open),
        "counted_usd": to_float(counted),
        "variance_usd": to_float(variance),
        "reconciled": not open_drawers,
        # 3. what is outstanding
        "account": account,
        # 4. everything else that moved
        "purchases": reports_service.purchases_summary(date, date),
        "stock_takes": stock_takes_for(date),
    }


def unbalanced(summary: dict, tolerance="0.01") -> bool:
    """True when a closed day's drawers did not come back to their expected sum."""
    return abs(D(summary["variance_usd"])) > D(tolerance)


def warnings(summary: dict) -> list[str]:
    """Plain-language reasons this day is not finished, worst first.

    The sheet leads with these because they are the things that get discovered a
    week later, when nobody remembers the day well enough to explain them.
    """
    notes: list[str] = []
    if summary["open_shifts"]:
        which = ", ".join(f"#{n}" for n in summary["open_shifts"])
        notes.append(
            f"Till {which} is still open - the drawer has not been counted, "
            "so the day does not reconcile yet."
        )
    if unbalanced(summary):
        variance = usd(summary["variance_usd"])
        notes.append(
            f"The counted cash is {'over' if variance > ZERO else 'short'} by "
            f"{abs(variance)} across the day."
        )
    if not summary["shift_count"] and summary["sale_count"]:
        notes.append("Sales were taken with no till shift open.")
    return notes
