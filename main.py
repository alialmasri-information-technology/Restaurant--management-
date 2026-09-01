#!/usr/bin/env python3
"""RE4 — Business & Retail Management.

Entry point. Run ``python main.py`` (add ``--demo`` on a fresh database to load
a sample catalogue, or ``--reset-admin`` if the administrator password is lost).
"""

from __future__ import annotations

import argparse
import sys


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RE4 - Business and Retail Management")
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
        "--data-dir",
        metavar="PATH",
        help="store the database and receipts in this folder instead of the default",
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    if args.data_dir:
        import os

        os.environ["RE4_DATA_DIR"] = args.data_dir

    from app import auth, config, db

    if args.reset_admin:
        db.init_db()
        row = db.query_one("SELECT user_id FROM users WHERE username = 'admin'")
        try:
            if row is None:
                auth.create_user("admin", args.reset_admin, config.ROLE_ADMIN, "Administrator")
                print("Created a new 'admin' account.")
            else:
                auth.set_password(row["user_id"], args.reset_admin)
                auth.set_active(row["user_id"], True)
                print("Password reset for 'admin'.")
        except auth.AuthError as exc:
            print(f"Could not reset the password: {exc}", file=sys.stderr)
            return 1
        return 0

    from app.ui.app import run

    run(seed_demo=args.demo)
    return 0


if __name__ == "__main__":
    sys.exit(main())
