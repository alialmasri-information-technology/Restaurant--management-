"""Housekeeping: what the shop leaves behind, tidied when the doors open.

Five things accumulate for as long as RE4 trades. Three are business records
the shop decides about — the audit trail, the stock movement history and the
printed receipts — and two are scratch work that should never outlive its
usefulness: held sales nobody came back for, and sign-in throttle rows for
accounts that no longer exist.

Everything here is conservative by default. Records are kept for ever unless an
administrator names a number of days, because deleting a receipt or an audit
line is a decision, not a side effect. The scratch items — parked sales and
throttle rows — have short defaults because keeping them buys nothing.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from app import config, db, logs
from app.services import settings as settings_service

#: A failed sign-in that has not been repeated for this long stops cluttering
#: the throttle table. Locked-out rows younger than this are left alone.
THROTTLE_AFTER_DAYS = 30


def _cutoff(days: int) -> str:
    """Local timestamp ``days`` ago, in the same format the tables store."""
    moment = dt.datetime.now() - dt.timedelta(days=days)
    return moment.isoformat(sep=" ", timespec="seconds")


def tidy() -> dict[str, int]:
    """Remove what has outlived its usefulness. Returns what was removed."""
    removed: dict[str, int] = {
        "throttle": _clear_stale_throttles(),
        "parked": _clear_old_parked(),
        "receipts": _clear_old_receipts(),
        "audit": _clear_old_audit(),
        "inventory": _clear_old_inventory(),
    }
    if any(removed.values()):
        logs.info(
            "Housekeeping: %d stale sign-in row(s), %d parked sale(s), "
            "%d receipt(s), %d audit row(s), %d stock movement(s) removed",
            removed["throttle"], removed["parked"], removed["receipts"],
            removed["audit"], removed["inventory"],
        )
        # A purge that deletes a chunk of the ledger leaves the WAL to match;
        # fold it back now rather than at closing time.
        db.checkpoint()
    return removed


def _clear_stale_throttles() -> int:
    cutoff = _cutoff(THROTTLE_AFTER_DAYS)
    with db.transaction() as conn:
        cursor = conn.execute(
            """
            DELETE FROM login_throttle
             WHERE last_fail_at < ?
               AND (locked_until IS NULL
                    OR datetime(locked_until) <= datetime('now', 'localtime'))
            """,
            (cutoff,),
        )
        return cursor.rowcount


def _clear_old_parked() -> int:
    days = settings_service.parked_keep_days()
    if not days:
        return 0
    with db.transaction() as conn:
        cursor = conn.execute(
            "DELETE FROM parked_sales WHERE created_at < ?", (_cutoff(days),)
        )
        return cursor.rowcount


def _clear_old_audit() -> int:
    days = settings_service.audit_keep_days()
    if not days:
        return 0
    with db.transaction() as conn:
        cursor = conn.execute(
            "DELETE FROM audit_log WHERE at < ?", (_cutoff(days),)
        )
        return cursor.rowcount


def _clear_old_inventory() -> int:
    """Trim the stock movement history.

    This is the "why is there one fewer than yesterday" log behind a product's
    movement list. It grows by a row per line sold, so it outpaces every other
    table in the file — and, unlike the audit trail, none of it is evidence
    about a person. It is still kept for ever by default: a shopkeeper who
    wants to know where the stock went a year ago should be able to find out.
    """
    days = settings_service.inventory_keep_days()
    if not days:
        return 0
    with db.transaction() as conn:
        cursor = conn.execute(
            "DELETE FROM inventory_log WHERE log_time < ?", (_cutoff(days),)
        )
        return cursor.rowcount


def _clear_old_receipts() -> int:
    days = settings_service.receipt_keep_days()
    if not days:
        return 0
    cutoff = (dt.datetime.now() - dt.timedelta(days=days)).timestamp()
    removed = 0
    for path in Path(config.RECEIPTS_DIR).glob("*"):
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            logs.warning("Housekeeping could not remove %s", path)
    return removed
