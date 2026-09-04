"""Authentication and user management.

Passwords are stored as salted PBKDF2-HMAC-SHA256 digests using only the
standard library, which keeps the frozen executable free of a compiled crypto
dependency. The stored format is::

    pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>

Comparisons use :func:`hmac.compare_digest` so verification is constant time.

Two policies live here rather than in the UI, so the command line and the tests
are held to them as well:

* **Throttling.** A run of wrong passwords locks the account for a few minutes.
  Without it, a PIN-length password on a shop counter is guessable in an
  afternoon, and the audit log only tells you so afterwards.
* **Hash upgrades.** :data:`ITERATIONS` rises over the years. A correct password
  is silently re-hashed at the current cost, so an account created in 2023 does
  not keep a 2023 work factor forever.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import os
import sqlite3
from dataclasses import dataclass

from app import config, db
from app.services import audit

ALGORITHM = "pbkdf2_sha256"
ITERATIONS = 260_000
SALT_BYTES = 16
MIN_PASSWORD_LENGTH = 6


class AuthError(Exception):
    """Raised for user-facing authentication or user-management failures."""


class AccountLocked(AuthError):
    """Raised when too many wrong passwords have locked an account."""

    def __init__(self, message: str, seconds_remaining: int = 0):
        super().__init__(message)
        self.seconds_remaining = seconds_remaining


@dataclass(frozen=True)
class User:
    user_id: int
    username: str
    role: str
    full_name: str
    is_active: bool = True
    must_change_password: bool = False

    @property
    def is_admin(self) -> bool:
        return self.role == config.ROLE_ADMIN

    @property
    def display_name(self) -> str:
        return self.full_name or self.username


def hash_password(password: str, *, iterations: int | None = None) -> str:
    """Hash at :data:`ITERATIONS` unless told otherwise.

    The cost is read at call time rather than bound as a default argument, so
    raising :data:`ITERATIONS` — or lowering it in the test suite, where 260,000
    rounds per sign-in buys nothing — takes effect without restarting.
    """
    iterations = ITERATIONS if iterations is None else iterations
    salt = os.urandom(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{ALGORITHM}${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, iterations, salt_hex, digest_hex = stored.split("$")
        if algorithm != ALGORITHM:
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations)
        )
    except (ValueError, AttributeError):
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)


def _row_to_user(row: sqlite3.Row) -> User:
    return User(
        user_id=row["user_id"],
        username=row["username"],
        role=row["role"],
        full_name=row["full_name"],
        is_active=bool(row["is_active"]),
        must_change_password=bool(_optional(row, "must_change_password", 0)),
    )


def _optional(row: sqlite3.Row, key: str, default=None):
    """Read a column that a database mid-migration may not carry yet."""
    # sqlite3.Row has no __contains__, so `key in row` would search the
    # values, not the column names. .keys() is the correct test here.
    return row[key] if key in row.keys() else default  # noqa: SIM118


def validate_credentials(username: str, password: str, *, require_password=True) -> None:
    if not username or not username.strip():
        raise AuthError("Username is required.")
    if len(username.strip()) < 3:
        raise AuthError("Username must be at least 3 characters.")
    checking_password = require_password or password
    if checking_password and len(password or "") < MIN_PASSWORD_LENGTH:
        raise AuthError(
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
        )


def create_user(
    username: str,
    password: str,
    role: str,
    full_name: str = "",
    *,
    must_change_password: bool = False,
) -> int:
    validate_credentials(username, password)
    if role not in config.ROLES:
        raise AuthError(f"Unknown role: {role}")
    try:
        user_id = db.execute(
            """
            INSERT INTO users
                (username, password_hash, role, full_name, must_change_password)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                username.strip(),
                hash_password(password),
                role,
                full_name.strip(),
                1 if must_change_password else 0,
            ),
        )
    except sqlite3.IntegrityError as exc:
        raise AuthError(f"The username '{username}' is already taken.") from exc
    audit.record("User created", "user", user_id, f"{username.strip()} ({role})")
    return user_id


# --------------------------------------------------------------------------- #
# Sign-in throttling
# --------------------------------------------------------------------------- #

_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def _now() -> dt.datetime:
    return dt.datetime.now().replace(microsecond=0)


def _parse_time(value) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.strptime(str(value)[:19], _TIME_FORMAT)
    except ValueError:
        return None


def _policy() -> tuple[int, int]:
    """(max consecutive failures, lockout minutes). Either at 0 disables it."""
    from app.services import settings as settings_service

    def read(key: str) -> int:
        try:
            return max(0, int(str(settings_service.get(key)).strip() or 0))
        except (TypeError, ValueError):
            return int(config.DEFAULT_SETTINGS[key])

    return read("login_max_attempts"), read("login_lockout_minutes")


def lockout_remaining(username: str) -> int:
    """Seconds until ``username`` may try again. 0 when it is not locked."""
    row = db.query_one(
        "SELECT locked_until FROM login_throttle WHERE username = ?",
        ((username or "").strip(),),
    )
    locked_until = _parse_time(row["locked_until"]) if row else None
    if locked_until is None:
        return 0
    return max(0, int((locked_until - _now()).total_seconds()))


def clear_lockout(username: str) -> None:
    """Forget a username's failed attempts — used on success and by an admin."""
    db.execute(
        "DELETE FROM login_throttle WHERE username = ?", ((username or "").strip(),)
    )


def locked_accounts() -> list[sqlite3.Row]:
    """Currently locked usernames, most recently locked first."""
    return db.query(
        """
        SELECT username, fail_count, last_fail_at, locked_until
        FROM login_throttle
        WHERE locked_until IS NOT NULL
          AND datetime(locked_until) > datetime('now', 'localtime')
        ORDER BY locked_until DESC
        """
    )


def _register_failure(username: str) -> int:
    """Count one wrong password. Returns the lockout in seconds, 0 if none."""
    username = (username or "").strip()
    max_attempts, lockout_minutes = _policy()
    if max_attempts <= 0 or lockout_minutes <= 0:
        return 0

    now = _now()
    stamp = now.strftime(_TIME_FORMAT)
    row = db.query_one(
        "SELECT fail_count FROM login_throttle WHERE username = ?", (username,)
    )
    fails = (row["fail_count"] if row else 0) + 1

    if fails >= max_attempts:
        locked_until = now + dt.timedelta(minutes=lockout_minutes)
        db.execute(
            """
            INSERT INTO login_throttle
                (username, fail_count, first_fail_at, last_fail_at, locked_until)
            VALUES (?, 0, ?, ?, ?)
            ON CONFLICT (username) DO UPDATE
                SET fail_count = 0, last_fail_at = excluded.last_fail_at,
                    locked_until = excluded.locked_until
            """,
            (username, stamp, stamp, locked_until.strftime(_TIME_FORMAT)),
        )
        audit.record(
            "Account locked",
            "user",
            "",
            f"{username}: {max_attempts} failed attempts, locked for "
            f"{lockout_minutes} minute(s)",
            user=None,
        )
        return lockout_minutes * 60

    db.execute(
        """
        INSERT INTO login_throttle
            (username, fail_count, first_fail_at, last_fail_at, locked_until)
        VALUES (?, ?, ?, ?, NULL)
        ON CONFLICT (username) DO UPDATE
            SET fail_count = excluded.fail_count, last_fail_at = excluded.last_fail_at
        """,
        (username, fails, stamp, stamp),
    )
    return 0


def _describe_lockout(seconds: int) -> str:
    minutes = max(1, round(seconds / 60))
    unit = "minute" if minutes == 1 else "minutes"
    return (
        f"Too many failed sign-in attempts. Try again in about {minutes} {unit}, "
        f"or ask an administrator to unlock the account."
    )


# --------------------------------------------------------------------------- #
# Sign-in
# --------------------------------------------------------------------------- #

def authenticate(username: str, password: str) -> User:
    """Return the matching active user, or raise :class:`AuthError`."""
    if not username or not password:
        raise AuthError("Enter both a username and a password.")

    username = username.strip()
    remaining = lockout_remaining(username)
    if remaining:
        audit.record("Sign-in blocked", "user", "", f"{username}: locked", user=None)
        raise AccountLocked(_describe_lockout(remaining), remaining)

    row = db.query_one("SELECT * FROM users WHERE username = ?", (username,))
    # Hash regardless of whether the user exists so a missing account and a
    # wrong password take the same amount of time.
    stored = row["password_hash"] if row else hash_password("dummy", iterations=1000)
    if not verify_password(password, stored) or row is None:
        locked_for = _register_failure(username)
        # Recorded without an actor: a failed attempt has no signed-in user, and
        # a run of these is exactly what someone reviewing the log wants to see.
        audit.record("Sign-in failed", "user", "", username, user=None)
        if locked_for:
            raise AccountLocked(_describe_lockout(locked_for), locked_for)
        raise AuthError("Invalid username or password.")
    if not row["is_active"]:
        audit.record(
            "Sign-in refused", "user", row["user_id"], "account deactivated", user=None
        )
        raise AuthError("This account has been deactivated. Contact an administrator.")

    clear_lockout(username)
    _upgrade_hash_if_stale(row["user_id"], row["password_hash"], password)
    db.execute(
        "UPDATE users SET last_login_at = datetime('now', 'localtime') WHERE user_id = ?",
        (row["user_id"],),
    )
    user = _row_to_user(row)
    audit.record("Signed in", "user", user.user_id, user.role, user=user)
    return user


def _upgrade_hash_if_stale(user_id: int, stored: str, password: str) -> None:
    """Re-hash at the current work factor once a password is known to be right."""
    try:
        _algorithm, iterations, _salt, _digest = stored.split("$")
        current = int(iterations)
    except (ValueError, AttributeError):
        return
    if current >= ITERATIONS:
        return
    db.execute(
        "UPDATE users SET password_hash = ? WHERE user_id = ?",
        (hash_password(password), user_id),
    )
    audit.record("Password hash upgraded", "user", user_id, f"{current} → {ITERATIONS}")


def verify_user_password(user_id: int, password: str) -> bool:
    """Check a password without signing anybody in.

    Used by the lock screen, which is re-confirming an operator who is already
    signed in — so it neither counts towards the lockout nor writes a sign-in to
    the audit trail, which would drown the real ones.
    """
    row = db.query_one("SELECT password_hash FROM users WHERE user_id = ?", (user_id,))
    if row is None or not password:
        return False
    return verify_password(password, row["password_hash"])


def get_user(user_id: int) -> User | None:
    row = db.query_one("SELECT * FROM users WHERE user_id = ?", (user_id,))
    return _row_to_user(row) if row else None


def list_users(include_inactive: bool = True) -> list[User]:
    sql = "SELECT * FROM users"
    if not include_inactive:
        sql += " WHERE is_active = 1"
    sql += " ORDER BY is_active DESC, username COLLATE NOCASE"
    return [_row_to_user(row) for row in db.query(sql)]


def count_active_admins(excluding: int | None = None) -> int:
    sql = "SELECT COUNT(*) FROM users WHERE role = ? AND is_active = 1"
    params: list = [config.ROLE_ADMIN]
    if excluding is not None:
        sql += " AND user_id != ?"
        params.append(excluding)
    return db.scalar(sql, tuple(params), default=0)


def update_user(user_id: int, *, username: str, role: str, full_name: str) -> None:
    validate_credentials(username, "", require_password=False)
    if role not in config.ROLES:
        raise AuthError(f"Unknown role: {role}")
    if role != config.ROLE_ADMIN and count_active_admins(excluding=user_id) == 0:
        raise AuthError("The last active administrator must keep the Admin role.")

    before = db.query_one("SELECT * FROM users WHERE user_id = ?", (user_id,))
    try:
        db.execute(
            "UPDATE users SET username = ?, role = ?, full_name = ? WHERE user_id = ?",
            (username.strip(), role, full_name.strip(), user_id),
        )
    except sqlite3.IntegrityError as exc:
        raise AuthError(f"The username '{username}' is already taken.") from exc

    if before is not None:
        detail = audit.describe_changes(
            dict(before),
            {"username": username.strip(), "role": role, "full_name": full_name.strip()},
            ["username", "role", "full_name"],
        )
        audit.record("User updated", "user", user_id, detail or "no changes")


def set_password(user_id: int, password: str, *, must_change: bool = False) -> None:
    """Set a password outright.

    ``must_change`` marks the account so the next sign-in has to choose a new
    one — what an administrator resetting somebody else's password wants, since
    the temporary password has necessarily been spoken out loud.
    """
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise AuthError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    row = db.query_one("SELECT username FROM users WHERE user_id = ?", (user_id,))
    db.execute(
        """
        UPDATE users SET password_hash = ?, must_change_password = ?
        WHERE user_id = ?
        """,
        (hash_password(password), 1 if must_change else 0, user_id),
    )
    # A forgotten password is the usual reason an account is locked out; a
    # successful reset should not leave the operator waiting out the lockout.
    if row is not None:
        clear_lockout(row["username"])
    # The password itself is never recorded, only that it changed.
    audit.record(
        "Password changed", "user", user_id,
        "reset by an administrator" if must_change else "",
    )


def change_password(user_id: int, current_password: str, new_password: str) -> None:
    row = db.query_one("SELECT password_hash FROM users WHERE user_id = ?", (user_id,))
    if row is None:
        raise AuthError("User not found.")
    if not verify_password(current_password, row["password_hash"]):
        raise AuthError("Your current password is not correct.")
    if verify_password(new_password, row["password_hash"]):
        raise AuthError("The new password must be different from the current one.")
    set_password(user_id, new_password)


def set_active(user_id: int, active: bool) -> None:
    if not active and count_active_admins(excluding=user_id) == 0:
        raise AuthError("You cannot deactivate the last active administrator.")
    db.execute(
        "UPDATE users SET is_active = ? WHERE user_id = ?",
        (1 if active else 0, user_id),
    )
    audit.record(
        "User reactivated" if active else "User deactivated", "user", user_id
    )


def delete_user(user_id: int) -> None:
    """Deactivate rather than delete, so past sales keep their cashier."""
    set_active(user_id, False)
