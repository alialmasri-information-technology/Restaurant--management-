"""Customer accounts: what is owed, and what has been paid off.

``Credit`` has always been one of the payment methods, but nothing recorded the
debt it created — a sale could go out of the door on account and the money was
simply forgotten. This module is the ledger behind it.

Every event that moves what a customer owes writes one signed row:

===========  ======  ==================================================
Kind         Sign    Written when
===========  ======  ==================================================
Sale         ``+``   an invoice is settled on account
Payment      ``-``   the customer pays some or all of it back
Refund       ``-``   goods from a credit sale come back
Adjustment   either  an owner writes a debt off, or corrects one
===========  ======  ==================================================

The balance is the sum of the rows, never a column that can drift out of step
with them. That costs one ``SUM`` per lookup and buys the thing a shopkeeper
actually needs when a customer disputes a figure: every penny explained, in
order, with the invoice or receipt number beside it.

Money is only ever *reduced* by a payment, never by editing history, so a
mistaken payment is reversed with an offsetting adjustment that both parties can
see rather than by deleting the row.
"""

from __future__ import annotations

import sqlite3

from app import config, db
from app.money import ZERO, D, to_float, usd
from app.services import audit
from app.services import settings as settings_service

KIND_SALE = "Sale"
KIND_PAYMENT = "Payment"
KIND_REFUND = "Refund"
KIND_ADJUSTMENT = "Adjustment"


class AccountError(Exception):
    """Raised for user-facing customer-account failures."""


# --------------------------------------------------------------------------- #
# Writing to the ledger
# --------------------------------------------------------------------------- #

def _add_entry(
    customer_id: int,
    kind: str,
    amount,
    *,
    sale_id: int | None = None,
    return_id: int | None = None,
    shift_id: int | None = None,
    user_id: int | None = None,
    method: str = "",
    reference: str = "",
    note: str = "",
    conn: sqlite3.Connection | None = None,
) -> int:
    """Append one signed row. ``amount`` is positive to increase the debt."""
    def _apply(connection: sqlite3.Connection) -> int:
        cursor = connection.execute(
            """
            INSERT INTO customer_ledger
                (customer_id, kind, amount_usd, sale_id, return_id, shift_id,
                 user_id, method, reference, note)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                customer_id,
                kind,
                to_float(amount),
                sale_id,
                return_id,
                shift_id,
                user_id,
                method or "",
                reference or "",
                (note or "").strip(),
            ),
        )
        return cursor.lastrowid

    if conn is not None:
        return _apply(conn)
    with db.transaction() as connection:
        return _apply(connection)


def charge_sale(
    customer_id: int,
    sale_id: int,
    amount,
    *,
    invoice_no: str = "",
    user_id: int | None = None,
    shift_id: int | None = None,
    conn: sqlite3.Connection | None = None,
) -> int:
    """Put an invoice on the customer's account. Called from the checkout."""
    return _add_entry(
        customer_id, KIND_SALE, usd(amount),
        sale_id=sale_id, user_id=user_id, shift_id=shift_id,
        method="Credit", reference=invoice_no, conn=conn,
    )


def credit_return(
    customer_id: int,
    return_id: int,
    amount,
    *,
    reference: str = "",
    sale_id: int | None = None,
    user_id: int | None = None,
    shift_id: int | None = None,
    conn: sqlite3.Connection | None = None,
) -> int:
    """Take goods back off a credit sale: the debt shrinks, no cash moves."""
    return _add_entry(
        customer_id, KIND_REFUND, -usd(amount),
        sale_id=sale_id, return_id=return_id, user_id=user_id, shift_id=shift_id,
        method="Credit", reference=reference, conn=conn,
    )


def record_payment(
    customer_id: int,
    amount,
    *,
    user_id: int | None = None,
    method: str = "Cash",
    paid_currency: str = "USD",
    exchange_rate=None,
    sale_id: int | None = None,
    note: str = "",
) -> int:
    """Take money against an account.

    Accepts LBP at the rate in force and converts, the same way the till does.
    A payment larger than the balance is refused rather than quietly leaving the
    account in credit: on a shop counter that is nearly always a typo, and an
    overpayment that is genuinely intended is an Adjustment an owner makes
    deliberately.
    """
    customer = db.query_one(
        "SELECT * FROM customers WHERE customer_id = ?", (customer_id,)
    )
    if customer is None:
        raise AccountError("Customer not found.")
    if method not in config.PAYMENT_METHODS:
        raise AccountError(f"Unknown payment method: {method}")
    if paid_currency not in config.CURRENCIES:
        raise AccountError(f"Unknown currency: {paid_currency}")

    amount = D(amount)
    if amount <= ZERO:
        raise AccountError("Enter a payment greater than zero.")

    if paid_currency == "LBP":
        rate = D(exchange_rate if exchange_rate is not None else settings_service.exchange_rate())
        if rate <= ZERO:
            raise AccountError("The USD → LBP exchange rate must be greater than zero.")
        amount = usd(amount / rate)
    else:
        amount = usd(amount)

    owed = balance(customer_id)
    if owed <= ZERO:
        raise AccountError(f"{customer['name']} does not owe anything.")
    if amount > owed:
        raise AccountError(
            f"{customer['name']} owes {usd(owed)}, which is less than the "
            f"{usd(amount)} offered. Take {usd(owed)} to settle the account."
        )

    # A cash payment lands in the drawer, so it belongs to the open shift and
    # has to reach the till's expected figure or the count will come up over.
    shift = _open_shift_id()

    entry_id = _add_entry(
        customer_id, KIND_PAYMENT, -amount,
        sale_id=sale_id, user_id=user_id, shift_id=shift, method=method,
        reference="", note=note,
    )
    audit.record(
        "Account payment", "customer", customer_id,
        f"{customer['name']}: {usd(amount)} by {method}"
        + (f" ({paid_currency})" if paid_currency != "USD" else "")
        + f", balance now {usd(owed - amount)}",
    )
    return entry_id


def adjust(customer_id: int, amount, *, user_id: int | None = None, reason: str = "") -> int:
    """Write a debt off, or correct one. ``amount`` is signed."""
    amount = usd(amount)
    if amount == ZERO:
        raise AccountError("Enter an adjustment other than zero.")
    if not (reason or "").strip():
        raise AccountError("Give a reason for the adjustment.")

    customer = db.query_one(
        "SELECT name FROM customers WHERE customer_id = ?", (customer_id,)
    )
    if customer is None:
        raise AccountError("Customer not found.")

    entry_id = _add_entry(
        customer_id, KIND_ADJUSTMENT, amount, user_id=user_id, note=reason
    )
    audit.record(
        "Account adjusted", "customer", customer_id,
        f"{customer['name']}: {usd(amount):+} — {reason.strip()}",
    )
    return entry_id


def _open_shift_id() -> int | None:
    from app.services import shifts as shifts_service

    shift = shifts_service.current_shift()
    return shift["shift_id"] if shift else None


# --------------------------------------------------------------------------- #
# Reading the ledger
# --------------------------------------------------------------------------- #

def balance(customer_id: int):
    """What the customer owes right now, as a Decimal. Negative means in credit."""
    total = db.scalar(
        "SELECT COALESCE(SUM(amount_usd), 0) FROM customer_ledger WHERE customer_id = ?",
        (customer_id,),
        default=0.0,
    )
    return usd(D(total or 0))


def statement(customer_id: int, limit: int = 200) -> list[sqlite3.Row]:
    """Every movement on the account, oldest first, with a running balance."""
    rows = db.query(
        """
        SELECT l.*, u.username,
               COALESCE(r.return_no, s.invoice_no, l.reference) AS document
        FROM customer_ledger l
        LEFT JOIN users u   ON u.user_id = l.user_id
        LEFT JOIN sales s   ON s.sale_id = l.sale_id
        LEFT JOIN returns r ON r.return_id = l.return_id
        WHERE l.customer_id = ?
        ORDER BY l.entry_id
        LIMIT ?
        """,
        (customer_id, limit),
    )
    running = ZERO
    out = []
    for row in rows:
        running += D(row["amount_usd"])
        entry = dict(row)
        entry["running_balance"] = to_float(running)
        out.append(entry)
    return out


def outstanding(limit: int = 200) -> list[sqlite3.Row]:
    """Customers who owe something, biggest debt first."""
    return db.query(
        """
        SELECT c.customer_id, c.name, c.phone, c.credit_limit_usd,
               SUM(l.amount_usd) AS balance_usd,
               MAX(l.at) AS last_movement,
               MIN(CASE WHEN l.kind = 'Sale' THEN l.at END) AS oldest_charge
        FROM customer_ledger l
        JOIN customers c ON c.customer_id = l.customer_id
        GROUP BY c.customer_id, c.name, c.phone, c.credit_limit_usd
        HAVING SUM(l.amount_usd) > 0.005
        ORDER BY balance_usd DESC
        LIMIT ?
        """,
        (limit,),
    )


def total_receivable():
    """Everything owed to the shop, as a Decimal."""
    total = db.scalar(
        """
        SELECT COALESCE(SUM(balance), 0) FROM (
            SELECT SUM(amount_usd) AS balance
            FROM customer_ledger
            GROUP BY customer_id
            HAVING SUM(amount_usd) > 0.005
        )
        """,
        default=0.0,
    )
    return usd(D(total or 0))


# --------------------------------------------------------------------------- #
# Credit limits
# --------------------------------------------------------------------------- #

def credit_limit(customer_id: int):
    """0 means no limit has been set, which is treated as 'no credit allowed'."""
    value = db.scalar(
        "SELECT credit_limit_usd FROM customers WHERE customer_id = ?", (customer_id,)
    )
    return usd(D(value or 0))


def set_credit_limit(customer_id: int, limit, *, user_id: int | None = None) -> None:
    limit = D(limit)
    if limit < ZERO:
        raise AccountError("A credit limit cannot be negative.")
    customer = db.query_one(
        "SELECT name, credit_limit_usd FROM customers WHERE customer_id = ?", (customer_id,)
    )
    if customer is None:
        raise AccountError("Customer not found.")
    db.execute(
        "UPDATE customers SET credit_limit_usd = ? WHERE customer_id = ?",
        (to_float(limit), customer_id),
    )
    audit.record(
        "Credit limit set", "customer", customer_id,
        f"{customer['name']}: {usd(customer['credit_limit_usd'])} → {usd(limit)}",
    )


def check_can_charge(customer_id: int | None, amount) -> None:
    """Raise if this sale cannot go on the customer's account.

    Called before the invoice is written, so a refusal costs the cashier nothing
    but a different payment method.
    """
    if customer_id is None:
        raise AccountError(
            "A sale on account needs a customer. Pick one, or take payment another way."
        )
    customer = db.query_one(
        "SELECT name, credit_limit_usd FROM customers WHERE customer_id = ?", (customer_id,)
    )
    if customer is None:
        raise AccountError("Customer not found.")

    limit = usd(D(customer["credit_limit_usd"] or 0))
    if limit <= ZERO:
        raise AccountError(
            f"{customer['name']} has no credit limit set, so nothing can go on "
            "account. Set one under Customers first."
        )

    owed = balance(customer_id)
    after = owed + usd(amount)
    if after > limit:
        raise AccountError(
            f"{customer['name']} owes {usd(owed)} and their limit is {usd(limit)}. "
            f"This sale would take them to {usd(after)}."
        )
