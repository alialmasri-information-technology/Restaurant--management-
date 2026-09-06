"""Audit trail — who did what, and when.

The signed-in user is remembered here so callers deep in the service layer can
record an action without every function having to thread a user through its
signature. Recording never raises: an audit failure must not roll back the
business action it was describing, but it is logged.
"""

from __future__ import annotations

import sqlite3

from app import db, logs

_actor = None  # app.auth.User of the signed-in operator


def set_actor(user) -> None:
    global _actor
    _actor = user


def current_actor():
    return _actor


def clear_actor() -> None:
    set_actor(None)


def record(action: str, entity: str = "", entity_id="", detail: str = "", user=None) -> None:
    """Append one line to the audit trail."""
    user = user or _actor
    try:
        db.execute(
            """
            INSERT INTO audit_log (user_id, username, action, entity, entity_id, detail)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                getattr(user, "user_id", None),
                getattr(user, "username", "") or "system",
                action,
                entity,
                "" if entity_id is None else str(entity_id),
                detail,
            ),
        )
    except sqlite3.Error:
        logs.exception("Could not write audit entry: %s %s %s", action, entity, entity_id)


def describe_changes(before: dict, after: dict, fields=None) -> str:
    """Render 'price 4.50 -> 5.00; reorder 5 -> 8' for the changed fields only."""
    parts = []
    for key in fields or after.keys():
        old = before.get(key)
        new = after.get(key)
        if old is None and new is None:
            continue
        if str(old) != str(new):
            parts.append(f"{key} {old!s} → {new!s}")
    return "; ".join(parts)


def list_entries(
    search: str = "",
    action: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 500,
) -> list[sqlite3.Row]:
    clauses: list[str] = []
    params: list = []

    if action and action != "All":
        clauses.append("action = ?")
        params.append(action)
    date_clauses, date_params = db.date_range_clauses("at", date_from, date_to)
    clauses += date_clauses
    params += date_params
    if search and search.strip():
        clauses.append("(username LIKE ? OR entity LIKE ? OR detail LIKE ? OR entity_id LIKE ?)")
        pattern = f"%{search.strip()}%"
        params += [pattern] * 4

    sql = "SELECT * FROM audit_log"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY audit_id DESC LIMIT ?"
    params.append(limit)
    return db.query(sql, tuple(params))


def known_actions() -> list[str]:
    return [row["action"] for row in db.query("SELECT DISTINCT action FROM audit_log ORDER BY action")]
