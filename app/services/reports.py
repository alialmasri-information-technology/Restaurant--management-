"""Aggregations for the dashboard and the reports screen.

Two rules run through every query here:

* **Returns are netted off, not filtered out.** Every sale counts towards gross
  revenue and every return is subtracted, so a partly returned invoice
  contributes exactly the part the customer kept.
* **Cost comes from the sale line, not the product.** ``sale_items.cost_usd`` is
  what the item actually cost when it was sold, so restocking at a new price
  never rewrites last month's margin.
"""

from __future__ import annotations

import datetime as dt
import sqlite3

from app import db

# Value of the units that have come back, per sale line.
RETURNED_VALUE = "(i.line_total_usd * i.returned_qty / i.qty)"
# Value of the units the customer kept.
KEPT_VALUE = "(i.line_total_usd * (i.qty - i.returned_qty) / i.qty)"


def today() -> str:
    return dt.date.today().isoformat()


def days_ago(days: int) -> str:
    return (dt.date.today() - dt.timedelta(days=days)).isoformat()


def month_start() -> str:
    return dt.date.today().replace(day=1).isoformat()


def previous_period(date_from: str, date_to: str) -> tuple[str, str]:
    """The equally long window immediately before ``date_from``.

    Used for the "vs" figures. Comparing this month against the whole of last
    month would flatter the first of the month and punish the last, so the
    comparison window is the same number of days, ending the day before.
    """
    start = dt.date.fromisoformat(date_from)
    end = dt.date.fromisoformat(date_to)
    span = (end - start).days
    previous_end = start - dt.timedelta(days=1)
    return (previous_end - dt.timedelta(days=span)).isoformat(), previous_end.isoformat()


def change_ratio(current, previous) -> float | None:
    """Fractional change from ``previous`` to ``current``.

    ``None`` when there is no baseline to compare against — "up 100%" from zero
    is arithmetic, not information.
    """
    try:
        current = float(current or 0)
        previous = float(previous or 0)
    except (TypeError, ValueError):
        return None
    if previous == 0:
        return None
    return (current - previous) / abs(previous)


def _range(date_from: str | None, date_to: str | None, column: str = "s.sale_time"):
    return db.date_range_clauses(column, date_from, date_to)


def _where(clauses) -> str:
    return (" WHERE " + " AND ".join(clauses)) if clauses else ""


def summary(date_from: str | None = None, date_to: str | None = None) -> dict:
    """Headline numbers for a date range (inclusive of both ends)."""
    clauses, params = _range(date_from, date_to)
    where = _where(clauses)

    sales = db.query_one(
        f"""
        SELECT COUNT(*) AS sale_count,
               COALESCE(SUM(s.total_usd), 0)    AS gross_revenue,
               COALESCE(SUM(s.discount_usd), 0) AS discounts,
               COALESCE(SUM(s.tax_usd), 0)      AS tax,
               COALESCE(SUM(CASE WHEN s.status = 'Refunded' THEN 1 ELSE 0 END), 0)
                   AS fully_refunded
        FROM sales s{where}
        """,
        tuple(params),
    )

    lines = db.query_one(
        f"""
        SELECT COALESCE(SUM(i.qty - i.returned_qty), 0) AS units,
               COALESCE(SUM((i.line_total_usd / i.qty - i.cost_usd)
                            * (i.qty - i.returned_qty)), 0) AS line_margin
        FROM sale_items i
        JOIN sales s ON s.sale_id = i.sale_id{where}
        """,
        tuple(params),
    )

    # The share of each invoice-level discount that was not handed back with a
    # return — mirrors how a refund is prorated in services/returns.py.
    discount_borne = db.scalar(
        f"""
        SELECT COALESCE(SUM(
            CASE WHEN s.subtotal_usd > 0 THEN
                s.discount_usd * (1 - (
                    SELECT COALESCE(SUM({RETURNED_VALUE}), 0)
                    FROM sale_items i WHERE i.sale_id = s.sale_id
                ) / s.subtotal_usd)
            ELSE 0 END), 0)
        FROM sales s{where}
        """,
        tuple(params),
        default=0.0,
    )

    return_clauses, return_params = _range(date_from, date_to, "r.created_at")
    refunds = db.query_one(
        f"""
        SELECT COUNT(*) AS return_count, COALESCE(SUM(r.total_usd), 0) AS returns_total
        FROM returns r{_where(return_clauses)}
        """,
        tuple(return_params),
    )

    gross = sales["gross_revenue"] or 0.0
    returned = refunds["returns_total"] or 0.0
    net_revenue = gross - returned
    net_sales = (sales["sale_count"] or 0) - (sales["fully_refunded"] or 0)

    return {
        "sale_count": net_sales,
        "gross_revenue": gross,
        "revenue": net_revenue,
        "discounts": sales["discounts"] or 0.0,
        "tax": sales["tax"] or 0.0,
        "units": lines["units"] or 0,
        "gross_profit": (lines["line_margin"] or 0.0) - (discount_borne or 0.0),
        "average_sale": (net_revenue / net_sales) if net_sales else 0.0,
        "refund_count": refunds["return_count"] or 0,
        "refund_total": returned,
    }


def daily_series(date_from: str, date_to: str) -> list[sqlite3.Row]:
    """Net revenue per day, oldest first. Days with no activity are omitted."""
    return db.query(
        """
        SELECT day, SUM(sale_count) AS sale_count, SUM(revenue) AS revenue FROM (
            SELECT date(sale_time) AS day, COUNT(*) AS sale_count,
                   COALESCE(SUM(total_usd), 0) AS revenue
            FROM sales
            WHERE sale_time >= date(?) AND sale_time < date(?, '+1 day')
            GROUP BY date(sale_time)
            UNION ALL
            SELECT date(created_at) AS day, 0 AS sale_count,
                   -COALESCE(SUM(total_usd), 0) AS revenue
            FROM returns
            WHERE created_at >= date(?) AND created_at < date(?, '+1 day')
            GROUP BY date(created_at)
        )
        GROUP BY day
        ORDER BY day
        """,
        (date_from, date_to, date_from, date_to),
    )


def top_products(
    date_from: str | None = None, date_to: str | None = None, limit: int = 10
) -> list[sqlite3.Row]:
    clauses, params = _range(date_from, date_to)
    return db.query(
        f"""
        SELECT i.name_at_sale AS name,
               i.sku_at_sale  AS sku,
               SUM(i.qty - i.returned_qty) AS units,
               SUM({KEPT_VALUE}) AS revenue,
               SUM((i.line_total_usd / i.qty - i.cost_usd) * (i.qty - i.returned_qty))
                   AS profit
        FROM sale_items i
        JOIN sales s ON s.sale_id = i.sale_id{_where(clauses)}
        GROUP BY i.name_at_sale, i.sku_at_sale
        HAVING units > 0
        ORDER BY revenue DESC
        LIMIT ?
        """,
        tuple(params) + (limit,),
    )


def top_customers(
    date_from: str | None = None, date_to: str | None = None, limit: int = 10
) -> list[sqlite3.Row]:
    clauses, params = _range(date_from, date_to)
    clauses.append("s.customer_id IS NOT NULL")
    return db.query(
        f"""
        SELECT c.name, COUNT(*) AS sale_count, SUM(s.total_usd) AS revenue
        FROM sales s
        JOIN customers c ON c.customer_id = s.customer_id{_where(clauses)}
        GROUP BY c.customer_id, c.name
        ORDER BY revenue DESC
        LIMIT ?
        """,
        tuple(params) + (limit,),
    )


def payment_breakdown(
    date_from: str | None = None, date_to: str | None = None
) -> list[sqlite3.Row]:
    clauses, params = _range(date_from, date_to)
    return db.query(
        f"""
        SELECT s.payment_method, COUNT(*) AS sale_count, SUM(s.total_usd) AS revenue
        FROM sales s{_where(clauses)}
        GROUP BY s.payment_method
        ORDER BY revenue DESC
        """,
        tuple(params),
    )


def sales_by_user(
    date_from: str | None = None, date_to: str | None = None
) -> list[sqlite3.Row]:
    clauses, params = _range(date_from, date_to)
    return db.query(
        f"""
        SELECT COALESCE(u.full_name, u.username, 'Unknown') AS name,
               COUNT(*) AS sale_count,
               SUM(s.total_usd) AS revenue
        FROM sales s
        LEFT JOIN users u ON u.user_id = s.user_id{_where(clauses)}
        GROUP BY s.user_id
        ORDER BY revenue DESC
        """,
        tuple(params),
    )


def returns_breakdown(
    date_from: str | None = None, date_to: str | None = None
) -> list[sqlite3.Row]:
    clauses, params = _range(date_from, date_to, "r.created_at")
    return db.query(
        f"""
        SELECT CASE WHEN TRIM(r.reason) = '' THEN 'Not given' ELSE r.reason END AS reason,
               COUNT(*) AS return_count,
               SUM(r.total_usd) AS refunded
        FROM returns r{_where(clauses)}
        GROUP BY reason
        ORDER BY refunded DESC
        """,
        tuple(params),
    )


def purchases_summary(
    date_from: str | None = None, date_to: str | None = None
) -> dict:
    clauses, params = _range(date_from, date_to, "o.received_at")
    clauses.append("o.status IN ('Received', 'Partially Received')")
    row = db.query_one(
        f"""
        SELECT COUNT(*) AS order_count, COALESCE(SUM(o.total_cost_usd), 0) AS ordered_value
        FROM purchase_orders o{_where(clauses)}
        """,
        tuple(params),
    )
    return dict(row) if row else {"order_count": 0, "ordered_value": 0.0}


def inventory_snapshot() -> dict:
    row = db.query_one(
        """
        SELECT COUNT(*) AS product_count,
               COALESCE(SUM(stock_qty), 0) AS units_in_stock,
               COALESCE(SUM(stock_qty * cost_usd), 0) AS stock_cost,
               COALESCE(SUM(stock_qty * price_usd), 0) AS stock_retail,
               COALESCE(SUM(CASE WHEN stock_qty <= reorder_level THEN 1 ELSE 0 END), 0)
                   AS low_stock_count,
               COALESCE(SUM(CASE WHEN stock_qty = 0 THEN 1 ELSE 0 END), 0)
                   AS out_of_stock_count
        FROM products
        WHERE is_active = 1
        """
    )
    return dict(row) if row else {}


def stock_valuation() -> list[sqlite3.Row]:
    """What the shelves are worth, grouped by category."""
    return db.query(
        """
        SELECT COALESCE(c.name, 'Uncategorised') AS category,
               COUNT(*) AS product_count,
               COALESCE(SUM(p.stock_qty), 0) AS units,
               COALESCE(SUM(p.stock_qty * p.cost_usd), 0) AS cost_value,
               COALESCE(SUM(p.stock_qty * p.price_usd), 0) AS retail_value
        FROM products p
        LEFT JOIN categories c ON c.category_id = p.category_id
        WHERE p.is_active = 1
        GROUP BY category
        ORDER BY cost_value DESC
        """
    )


def dead_stock(days: int = 60) -> list[sqlite3.Row]:
    """Products with stock that have not sold in the given window."""
    cutoff = days_ago(days)
    return db.query(
        """
        SELECT * FROM (
            SELECT p.product_id, p.sku, p.name, p.stock_qty,
                   (p.stock_qty * p.cost_usd) AS tied_up,
                   (SELECT MAX(s.sale_time) FROM sale_items i
                     JOIN sales s ON s.sale_id = i.sale_id
                    WHERE i.product_id = p.product_id) AS last_sold
            FROM products p
            WHERE p.is_active = 1 AND p.stock_qty > 0
        )
        WHERE last_sold IS NULL OR last_sold < date(?)
        ORDER BY tied_up DESC
        LIMIT 50
        """,
        (cutoff,),
    )
