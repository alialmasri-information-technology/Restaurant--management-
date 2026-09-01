"""Password hashing, sign-in, and the guards around administrator accounts."""

from __future__ import annotations

import unittest

from app import auth, config
from tests.support import DatabaseTestCase


class HashingTests(unittest.TestCase):
    def test_hash_is_salted(self):
        first = auth.hash_password("hunter22")
        second = auth.hash_password("hunter22")
        self.assertNotEqual(first, second)
        self.assertTrue(auth.verify_password("hunter22", first))
        self.assertTrue(auth.verify_password("hunter22", second))

    def test_wrong_password_fails(self):
        self.assertFalse(auth.verify_password("nope", auth.hash_password("hunter22")))

    def test_plaintext_is_never_stored(self):
        self.assertNotIn("hunter22", auth.hash_password("hunter22"))

    def test_corrupt_hash_does_not_crash(self):
        for stored in ("", "garbage", "pbkdf2_sha256$notanumber$aa$bb", None):
            self.assertFalse(auth.verify_password("x", stored))


class AuthenticationTests(DatabaseTestCase):
    def test_seeded_admin_can_sign_in(self):
        self.assertEqual(self.admin.username, "admin")
        self.assertTrue(self.admin.is_admin)

    def test_wrong_password_is_rejected(self):
        with self.assertRaises(auth.AuthError):
            auth.authenticate("admin", "wrong")

    def test_unknown_user_is_rejected(self):
        with self.assertRaises(auth.AuthError):
            auth.authenticate("nobody", "whatever")

    def test_blank_credentials_are_rejected(self):
        with self.assertRaises(auth.AuthError):
            auth.authenticate("", "")

    def test_deactivated_user_cannot_sign_in(self):
        auth.create_user("sara", "secret123", config.ROLE_EMPLOYEE, "Sara K")
        user = auth.authenticate("sara", "secret123")
        auth.set_active(user.user_id, False)
        with self.assertRaises(auth.AuthError):
            auth.authenticate("sara", "secret123")

    def test_username_is_case_insensitive(self):
        auth.create_user("Sara", "secret123", config.ROLE_EMPLOYEE)
        self.assertEqual(auth.authenticate("sara", "secret123").username, "Sara")


class UserManagementTests(DatabaseTestCase):
    def test_duplicate_username_is_refused(self):
        with self.assertRaises(auth.AuthError):
            auth.create_user("admin", "secret123", config.ROLE_ADMIN)

    def test_short_password_is_refused(self):
        with self.assertRaises(auth.AuthError):
            auth.create_user("sara", "12", config.ROLE_EMPLOYEE)

    def test_unknown_role_is_refused(self):
        with self.assertRaises(auth.AuthError):
            auth.create_user("sara", "secret123", "Wizard")

    def test_last_admin_cannot_be_deactivated(self):
        with self.assertRaises(auth.AuthError):
            auth.set_active(self.admin.user_id, False)

    def test_last_admin_cannot_be_demoted(self):
        with self.assertRaises(auth.AuthError):
            auth.update_user(
                self.admin.user_id, username="admin", role=config.ROLE_EMPLOYEE,
                full_name="Administrator",
            )

    def test_admin_can_be_demoted_when_another_admin_exists(self):
        auth.create_user("boss", "secret123", config.ROLE_ADMIN)
        auth.update_user(
            self.admin.user_id, username="admin", role=config.ROLE_EMPLOYEE,
            full_name="Administrator",
        )
        self.assertEqual(auth.get_user(self.admin.user_id).role, config.ROLE_EMPLOYEE)

    def test_change_password_requires_the_current_one(self):
        with self.assertRaises(auth.AuthError):
            auth.change_password(self.admin.user_id, "wrong", "newsecret")
        auth.change_password(self.admin.user_id, "admin123", "newsecret")
        self.assertTrue(auth.authenticate("admin", "newsecret"))

    def test_delete_deactivates_rather_than_removing(self):
        user_id = auth.create_user("sara", "secret123", config.ROLE_EMPLOYEE)
        auth.delete_user(user_id)
        self.assertFalse(auth.get_user(user_id).is_active)


if __name__ == "__main__":
    unittest.main()
