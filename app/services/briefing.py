"""What actually needs somebody, right now.

The dashboard was a wall of numbers. Numbers are what you look at when you have
a question; a briefing is what you want when you have just walked in and do not
yet know what the question is. The till has been open since yesterday, four
things are off the shelf, a customer is at their credit limit — none of that is
visible in a revenue figure, and all of it changes what you do next.

Each note is one sentence a person would actually say, with the screen that
fixes it attached, so the dashboard can make it clickable. Notes are ordered by
how much they cost to ignore: money that cannot be reconciled first, then money
that is owed, then stock, then housekeeping.

Nothing here is decorative. If a note is printed, something is genuinely
outstanding — which is what makes an empty briefing worth trusting.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from app import config, db
from app.money import fmt_usd

#: How stale a backup has to be before it is worth mentioning.
BACKUP_STALE_DAYS = 7

# Severity, low to high. The dashboard colours by this and sorts on it.
CALM = "calm"
NOTE = "note"
WARN = "warn"
_ORDER = {WARN: 0, NOTE: 1, CALM: 2}


@dataclass(frozen=True)
class Note:
    """One sentence, the screen that answers it, and how loudly to say it."""

    text: str
    screen: str = ""
    tone: str = NOTE

    def __str__(self) -> str:  # pragma: no cover - convenience only
        return self.text


def _plural(count: int, singular: str, plural_form: str = "") -> str:
    """Kept local so the service layer stays free of UI imports."""
    if count == 1:
        return f"1 {singular}"
    return f"{count:,} {plural_form or singular + 's'}"


# --------------------------------------------------------------------------- #
# The individual checks
# --------------------------------------------------------------------------- #

def _till(now: dt.datetime) -> list[Note]:
    from app.services import shifts as shifts_service

    shift = shifts_service.current_shift()
    if shift is None:
        if not shifts_service.shift_required():
            return []
        return [Note("The till is closed — open it before selling.", "till", WARN)]

    opened = str(shift["opened_at"] or "")[:10]
    if opened and opened != now.date().isoformat():
        return [Note(
            f"The till has been open since {opened}. Close it so the day can be "
            "reconciled.", "till", WARN,
        )]
    return []


def _accounts() -> list[Note]:
    from app.services import accounts as accounts_service

    debtors = accounts_service.outstanding()
    if not debtors:
        return []

    total = accounts_service.total_receivable()
    notes = [Note(
        f"{_plural(len(debtors), 'customer')} "
        f"{'owes' if len(debtors) == 1 else 'owe'} you {fmt_usd(total)}.",
        "customers", NOTE,
    )]

    # Being at the limit is the actionable half: the next sale on account is
    # going to be refused at the counter, in front of the customer.
    at_limit = [
        row["name"] for row in debtors
        if row["credit_limit_usd"] and row["balance_usd"] >= row["credit_limit_usd"] - 0.005
    ]
    if at_limit:
        names = ", ".join(at_limit[:3])
        more = f" and {len(at_limit) - 3} more" if len(at_limit) > 3 else ""
        notes.append(Note(
            f"{names}{more} {'is' if len(at_limit) == 1 else 'are'} at their credit "
            "limit and cannot buy on account.", "customers", WARN,
        ))
    return notes


def _stock() -> list[Note]:
    from app.services import products as products_service

    low = products_service.low_stock_products()
    if not low:
        return []
    out = sum(1 for row in low if row["stock_qty"] <= 0)
    if not out:
        return [Note(
            f"{_plural(len(low), 'product')} below the reorder level.", "products", NOTE
        )]

    text = f"{_plural(out, 'product')} completely out of stock"
    if len(low) > out:
        text += f", {len(low) - out} more running low"
    return [Note(f"{text}.", "products", WARN)]


def _stock_take() -> list[Note]:
    from app.services import stocktake as stocktake_service

    count = stocktake_service.current_count()
    if count is None:
        return []
    progress = stocktake_service.summary(count["stock_take_id"])
    return [Note(
        f"Stock take {count['reference']} is open — "
        f"{progress['counted_lines']} of {progress['line_count']} lines counted.",
        "stocktake", NOTE,
    )]


def _parked() -> list[Note]:
    from app.services import sales as sales_service

    parked = sales_service.list_parked()
    if not parked:
        return []
    return [Note(
        f"{_plural(len(parked), 'sale')} still parked, waiting to be finished.",
        "pos", NOTE,
    )]


def _purchases() -> list[Note]:
    rows = db.query(
        "SELECT COUNT(*) AS n FROM purchase_orders WHERE status IN (?, ?)",
        (config.PO_ORDERED, config.PO_PARTIAL),
    )
    waiting = rows[0]["n"] if rows else 0
    if not waiting:
        return []
    return [Note(
        f"{_plural(waiting, 'purchase order')} still waiting to be received.",
        "purchasing", NOTE,
    )]


def _housekeeping(now: dt.datetime) -> list[Note]:
    from app.services import backups as backups_service

    notes: list[Note] = []

    locked = db.query(
        "SELECT username FROM login_throttle WHERE locked_until IS NOT NULL "
        "AND datetime(locked_until) > datetime('now', 'localtime')"
    )
    if locked:
        names = ", ".join(row["username"] for row in locked)
        notes.append(Note(f"Locked out of their account: {names}.", "users", WARN))

    entries = backups_service.list_backups()
    if not entries:
        notes.append(Note("No backup has ever been taken.", "settings", WARN))
    else:
        try:
            last = dt.datetime.strptime(entries[0]["taken_at"], "%Y-%m-%d %H:%M")
        except (ValueError, KeyError):  # pragma: no cover - a hand-edited folder
            return notes
        days = (now - last).days
        if days >= BACKUP_STALE_DAYS:
            notes.append(Note(
                f"The last backup was {_plural(days, 'day')} ago.", "settings", NOTE,
            ))
    return notes


#: Which checks an employee is shown. The rest lead to screens they cannot open,
#: and a note somebody is not allowed to act on is just noise.
STAFF_CHECKS = ("till", "accounts", "stock", "stock_take", "parked")


# --------------------------------------------------------------------------- #
# The whole briefing
# --------------------------------------------------------------------------- #

def notes(*, is_admin: bool = True, now=None) -> list[Note]:
    """Everything outstanding, most costly to ignore first."""
    now = now or dt.datetime.now()
    checks = {
        "till": lambda: _till(now),
        "accounts": _accounts,
        "stock": _stock,
        "stock_take": _stock_take,
        "parked": _parked,
        "purchases": _purchases,
        "housekeeping": lambda: _housekeeping(now),
    }
    if not is_admin:
        checks = {name: check for name, check in checks.items() if name in STAFF_CHECKS}

    found: list[Note] = []
    for check in checks.values():
        found.extend(check())
    # Stable within a tone, so the order does not shuffle between refreshes.
    return sorted(found, key=lambda note: _ORDER.get(note.tone, 1))


def all_clear(is_admin: bool = True) -> bool:
    return not notes(is_admin=is_admin)
