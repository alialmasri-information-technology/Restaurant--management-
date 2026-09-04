#!/usr/bin/env python3
"""RE4 — Business & Retail Management.

Entry point. ``python main.py`` opens the shop.

The other options exist for the moment something has gone wrong and the person
in front of the machine cannot get into the user interface to fix it: a lost
administrator password, an account locked out by a run of typos, a database that
needs checking after a power cut. Each does one thing and exits.
"""

from __future__ import annotations

import argparse
import sys


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="re4",
        description="RE4 - Business and Retail Management",
        epilog=(
            "Recovery options run without opening the application and exit when "
            "they are done."
        ),
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="print the version and exit",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="seed demo categories and products (skipped if any product exists)",
    )
    parser.add_argument(
        "--reset-admin",
        metavar="PASSWORD",
        help="reset the 'admin' account password and exit",
    )
    parser.add_argument(
        "--unlock",
        metavar="USERNAME",
        help="clear a sign-in lockout on an account and exit ('all' for every one)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the database and print a summary of what is in it, then exit",
    )
    parser.add_argument(
        "--backup",
        action="store_true",
        help="write a backup of the database and exit",
    )
    parser.add_argument(
        "--data-dir",
        metavar="PATH",
        help="store the database and receipts in this folder instead of the default",
    )
    return parser.parse_args(argv)


def _reset_admin(password: str) -> int:
    from app import auth, config, db

    row = db.query_one("SELECT user_id FROM users WHERE username = 'admin'")
    try:
        if row is None:
            auth.create_user("admin", password, config.ROLE_ADMIN, "Administrator")
            print("Created a new 'admin' account.")
        else:
            auth.set_password(row["user_id"], password)
            auth.set_active(row["user_id"], True)
            print("Password reset for 'admin'.")
    except auth.AuthError as exc:
        print(f"Could not reset the password: {exc}", file=sys.stderr)
        return 1
    return 0


def _unlock(username: str) -> int:
    from app import auth

    locked = auth.locked_accounts()
    if username.lower() == "all":
        if not locked:
            print("No accounts are locked.")
            return 0
        for row in locked:
            auth.clear_lockout(row["username"])
        print(f"Unlocked: {', '.join(row['username'] for row in locked)}")
        return 0

    if not auth.lockout_remaining(username):
        print(f"'{username}' is not locked out.")
        return 0
    auth.clear_lockout(username)
    print(f"'{username}' can sign in again.")
    return 0


def _check() -> int:
    """A support call in one command: is the file sound, and what is in it?"""
    from app import config, db

    result = db.integrity_check()
    print(f"{config.APP_NAME} {config.APP_VERSION}")
    print(f"Database: {db.database_path()}")
    print(f"Schema:   v{db.scalar('PRAGMA user_version', default=0)} "
          f"(this build expects v{db.SCHEMA_VERSION})")
    print(f"Integrity: {result}")

    counts = (
        ("Products", "SELECT COUNT(*) FROM products WHERE is_active = 1"),
        ("Sales", "SELECT COUNT(*) FROM sales"),
        ("Customers", "SELECT COUNT(*) FROM customers"),
        ("Users", "SELECT COUNT(*) FROM users WHERE is_active = 1"),
        ("Open till shifts", "SELECT COUNT(*) FROM shifts WHERE status = 'Open'"),
        ("Open stock takes", "SELECT COUNT(*) FROM stock_takes WHERE status = 'Open'"),
    )
    print()
    for label, sql in counts:
        print(f"  {label + ':':<20}{db.scalar(sql, default=0)}")

    from app.money import fmt_usd
    from app.services import accounts

    owed = accounts.total_receivable()
    if owed:
        debtors = len(accounts.outstanding())
        print(f"  {'Owed on account:':<20}{fmt_usd(owed)} from {debtors} customer(s)")

    locked = db.query(
        "SELECT username FROM login_throttle WHERE locked_until IS NOT NULL "
        "AND datetime(locked_until) > datetime('now', 'localtime')"
    )
    if locked:
        print()
        print("  Locked out: " + ", ".join(row["username"] for row in locked))
        print("  Clear with --unlock all")

    return 0 if result == "ok" else 2


def _backup() -> int:
    from app.services import backups

    try:
        path = backups.create("manual")
    except backups.BackupError as exc:
        print(f"Backup failed: {exc}", file=sys.stderr)
        return 1
    print(f"Backup written to {path}")
    return 0


def main(argv=None) -> int:
    args = parse_args(argv)

    if args.version:
        from app import config

        print(f"{config.APP_NAME} {config.APP_VERSION}")
        return 0

    if args.data_dir:
        import os

        os.environ["RE4_DATA_DIR"] = args.data_dir

    from app import db

    # Every command below needs a database, and init_db is idempotent.
    if args.reset_admin or args.unlock or args.check or args.backup:
        db.init_db()

    if args.reset_admin:
        return _reset_admin(args.reset_admin)
    if args.unlock:
        return _unlock(args.unlock)
    if args.check:
        return _check()
    if args.backup:
        return _backup()

    from app.ui.app import run

    run(seed_demo=args.demo)
    return 0


if __name__ == "__main__":
    sys.exit(main())
