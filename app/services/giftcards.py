"""Gift cards: sold at the till, redeemed at the till, explained line by line.

A gift card is a small liability with a paper trail. Its balance is whatever
was loaded onto it less whatever has been spent from it, and every movement is
an event row — the card's history reads like the customer ledger reads, one
signed entry at a time.

Cards are sold through the till as a line on a sale, so the money arrives in
the drawer and the revenue is recorded by exactly the machinery every other
sale uses. The card itself is activated when that sale commits.
"""

from __future__ import annotations

import random
import sqlite3

from app import db
from app.money import ZERO, D, usd
from app.services import audit

ACTIVE = "Active"
EMPTY = "Empty"
DISABLED = "Disabled"
STATUSES = (ACTIVE, EMPTY, DISABLED)

# Unambiguous characters only: nobody should have to guess between a sold
# gift card's 0 and its O at the counter.
CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"


class GiftCardError(Exception):
    """Raised for user-facing gift card failures."""


def generate_code() -> str:
    body = "".join(random.SystemRandom().choice(CODE_ALPHABET) for _ in range(8))
    return f"GC-{body[:4]}-{body[4:]}"


def normalise(code: str) -> str:
    return (code or "").strip().upper()


def issue(initial_usd, user_id: int | None = None, note: str = "") -> dict:
    """Load a new card. The shop owes this value until it is spent."""
    value = D(initial_usd)
    if value <= ZERO:
        raise GiftCardError("A gift card has to be loaded with something.")
    value = usd(value)
    code = generate_code()
    card_id = db.execute(
        """
        INSERT INTO gift_cards (code, balance_usd, initial_usd, status, note)
        VALUES (?, ?, ?, ?, ?)
        """,
        (code, float(value), float(value), ACTIVE, (note or "").strip()),
    )
    db.execute(
        """
        INSERT INTO gift_card_events (card_id, kind, amount_usd, user_id, note)
        VALUES (?, 'Issue', ?, ?, ?)
        """,
        (card_id, float(value), user_id, (note or "").strip()),
    )
    audit.record("Gift card issued", "gift_card", card_id, f"{code}: {value}")
    card = get_by_id(card_id)
    assert card is not None
    return dict(card)


def get_by_id(card_id: int) -> sqlite3.Row | None:
    return db.query_one("SELECT * FROM gift_cards WHERE card_id = ?", (card_id,))


def get_by_code(code: str) -> sqlite3.Row | None:
    return db.query_one(
        "SELECT * FROM gift_cards WHERE code = ?", (normalise(code),)
    )


def balance(code: str) -> dict:
    """What a card is worth right now, or why the till will not take it."""
    card = get_by_code(code)
    if card is None:
        raise GiftCardError(f"No gift card matches '{(code or '').strip()}'.")
    return dict(card)


def list_cards(search: str = "") -> list[sqlite3.Row]:
    clauses, params = [], []
    if search and search.strip():
        clauses.append("(g.code LIKE ? OR g.note LIKE ?)")
        pattern = f"%{search.strip()}%"
        params += [pattern, pattern]
    sql = """
        SELECT g.*,
               (SELECT COUNT(*) FROM gift_card_events e WHERE e.card_id = g.card_id)
                   AS event_count
        FROM gift_cards g
    """
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY g.card_id DESC"
    return db.query(sql, tuple(params))


def history(card_id: int, limit: int = 50) -> list[sqlite3.Row]:
    return db.query(
        """
        SELECT e.*, u.username
        FROM gift_card_events e
        LEFT JOIN users u ON u.user_id = e.user_id
        WHERE e.card_id = ?
        ORDER BY e.event_id DESC
        LIMIT ?
        """,
        (card_id, limit),
    )


def set_status(card_id: int, status: str) -> None:
    if status not in STATUSES:
        raise GiftCardError(f"Unknown status: {status}")
    card = get_by_id(card_id)
    if card is None:
        raise GiftCardError("That gift card no longer exists.")
    db.execute(
        "UPDATE gift_cards SET status = ? WHERE card_id = ?", (status, card_id)
    )
    audit.record("Gift card status changed", "gift_card", card_id,
                 f"{card['code']}: {status}")


def activate(conn: sqlite3.Connection, code: str, value, sale_id: int,
             user_id: int | None) -> int:
    """Bring a sold card to life inside the sale's transaction.

    The card's row is created only when the sale commits, so a sale that is
    rolled back never leaves a live card behind. Returns the card id.
    """
    value = D(value)
    if value <= ZERO:
        raise GiftCardError("A gift card has to be loaded with something.")
    value = usd(value)
    cursor = conn.execute(
        """
        INSERT INTO gift_cards (code, balance_usd, initial_usd, status, sold_usd,
                                sold_sale_id)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (normalise(code), float(value), float(value), ACTIVE, float(value), sale_id),
    )
    card_id = cursor.lastrowid
    conn.execute(
        """
        INSERT INTO gift_card_events (card_id, kind, amount_usd, sale_id, user_id, note)
        VALUES (?, 'Sale', ?, ?, ?, 'Sold at the till')
        """,
        (card_id, float(value), sale_id, user_id),
    )
    return card_id


def redeem(conn: sqlite3.Connection, code: str, amount, sale_id: int,
           user_id: int | None) -> None:
    """Spend from a card, inside the sale's transaction.

    The balance is re-read and re-checked here, in the same transaction that
    writes the sale — two tills cannot both spend the last dollar, for the
    same reason they cannot both sell the last unit.
    """
    amount = usd(D(amount))
    if amount <= ZERO:
        raise GiftCardError("A gift card payment must be more than nothing.")
    row = conn.execute(
        "SELECT * FROM gift_cards WHERE code = ?", (normalise(code),)
    ).fetchone()
    if row is None:
        raise GiftCardError(f"No gift card matches '{(code or '').strip()}'.")
    if row["status"] == DISABLED:
        raise GiftCardError(f"Gift card {row['code']} has been disabled.")
    if row["status"] == EMPTY or D(row["balance_usd"]) <= ZERO:
        raise GiftCardError(f"Gift card {row['code']} is empty.")
    if D(row["balance_usd"]) < amount:
        raise GiftCardError(
            f"Gift card {row['code']} holds {usd(D(row['balance_usd']))}, "
            f"not the {amount} asked of it."
        )
    remaining = usd(D(row["balance_usd"]) - amount)
    conn.execute(
        "UPDATE gift_cards SET balance_usd = ?, status = ? WHERE card_id = ?",
        (
            float(remaining),
            EMPTY if remaining <= ZERO else ACTIVE,
            row["card_id"],
        ),
    )
    conn.execute(
        """
        INSERT INTO gift_card_events (card_id, kind, amount_usd, sale_id, user_id, note)
        VALUES (?, 'Redeem', ?, ?, ?, ?)
        """,
        (row["card_id"], -float(amount), sale_id, user_id, f"On sale {sale_id}"),
    )


def outstanding_liability() -> float:
    """What the shop still owes every card together — the shelf value."""
    row = db.query_one(
        "SELECT COALESCE(SUM(balance_usd), 0) AS total FROM gift_cards "
        "WHERE status IN (?, ?)",
        (ACTIVE, EMPTY),
    )
    return float(row["total"]) if row else 0.0


def totals() -> dict:
    return {
        "cards": db.scalar("SELECT COUNT(*) FROM gift_cards", default=0),
        "active": db.scalar(
            "SELECT COUNT(*) FROM gift_cards WHERE status = ?", (ACTIVE,), default=0
        ),
        "liability_usd": outstanding_liability(),
    }
