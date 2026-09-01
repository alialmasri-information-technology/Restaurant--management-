"""Authentication and user management.

Passwords are stored as salted PBKDF2-HMAC-SHA256 digests using only the
standard library, which keeps the frozen executable free of a compiled crypto
dependency. The stored format is::

    pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>

Comparisons use :func:`hmac.compare_digest` so verification is constant time.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import sqlite3
from dataclasses import dataclass

from app import config, db

ALGORITHM = "pbkdf2_sha256"
ITERATIONS = 260_000
SALT_BYTES = 16
MIN_PASSWORD_LENGTH = 6


class AuthError(Exception):
    """Raised for user-facing authentication or user-management failures."""


@dataclass(frozen=True)
class User:
    user_id: int
    username: str
    role: str
    full_name: str
    is_active: bool = True

    @property
    def is_admin(self) -> bool:
        return self.role == config.ROLE_ADMIN

    @property
    def display_name(self) -> str:
        return self.full_name or self.username


def hash_password(password: str, *, iterations: int = ITERATIONS) -> str:
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
    )


def validate_credentials(username: str, password: str, *, require_password=True) -> None:
    if not username or not username.strip():
        raise AuthError("Username is required.")
    if len(username.strip()) < 3:
        raise AuthError("Username must be at least 3 characters.")
    if require_password or password:
        if len(password or "") < MIN_PASSWORD_LENGTH:
            raise AuthError(
                f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
            )


def create_user(username: str, password: str, role: str, full_name: str = "") -> int:
    validate_credentials(username, password)
    if role not in config.ROLES:
        raise AuthError(f"Unknown role: {role}")
    try:
        return db.execute(
            """
            INSERT INTO users (username, password_hash, role, full_name)
            VALUES (?, ?, ?, ?)
            """,
            (username.strip(), hash_password(password), role, full_name.strip()),
        )
    except sqlite3.IntegrityError as exc:
        raise AuthError(f"The username '{username}' is already taken.") from exc


def authenticate(username: str, password: str) -> User:
    """Return the matching active user, or raise :class:`AuthError`."""
    if not username or not password:
        raise AuthError("Enter both a username and a password.")

    row = db.query_one(
        "SELECT * FROM users WHERE username = ?",
        (username.strip(),),
    )
    # Hash regardless of whether the user exists so a missing account and a
    # wrong password take the same amount of time.
    stored = row["password_hash"] if row else hash_password("dummy", iterations=1000)
    if not verify_password(password, stored) or row is None:
        raise AuthError("Invalid username or password.")
    if not row["is_active"]:
        raise AuthError("This account has been deactivated. Contact an administrator.")
    return _row_to_user(row)


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
    try:
        db.execute(
            "UPDATE users SET username = ?, role = ?, full_name = ? WHERE user_id = ?",
            (username.strip(), role, full_name.strip(), user_id),
        )
    except sqlite3.IntegrityError as exc:
        raise AuthError(f"The username '{username}' is already taken.") from exc


def set_password(user_id: int, password: str) -> None:
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise AuthError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    db.execute(
        "UPDATE users SET password_hash = ? WHERE user_id = ?",
        (hash_password(password), user_id),
    )


def change_password(user_id: int, current_password: str, new_password: str) -> None:
    row = db.query_one("SELECT password_hash FROM users WHERE user_id = ?", (user_id,))
    if row is None:
        raise AuthError("User not found.")
    if not verify_password(current_password, row["password_hash"]):
        raise AuthError("Your current password is not correct.")
    set_password(user_id, new_password)


def set_active(user_id: int, active: bool) -> None:
    if not active and count_active_admins(excluding=user_id) == 0:
        raise AuthError("You cannot deactivate the last active administrator.")
    db.execute(
        "UPDATE users SET is_active = ? WHERE user_id = ?",
        (1 if active else 0, user_id),
    )


def delete_user(user_id: int) -> None:
    """Deactivate rather than delete, so past sales keep their cashier."""
    set_active(user_id, False)
