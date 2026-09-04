"""Sign-in policy: throttling, hash upgrades and forced password changes."""

from __future__ import annotations

import datetime as dt

from app import auth, config, db
from app.services import audit as audit_service
from app.services import settings as settings_service
from tests.support import DatabaseTestCase


class LockoutTests(DatabaseTestCase):
    opens_shift = False

    def setUp(self) -> None:
        super().setUp()
        settings_service.set_many({"login_max_attempts": "3", "login_lockout_minutes": "5"})
        self.user_id = auth.create_user("cashier", "correct-horse", config.ROLE_EMPLOYEE)

    def fail_once(self, password: str = "wrong") -> None:
        with self.assertRaises(auth.AuthError):
            auth.authenticate("cashier", password)

    def test_a_wrong_password_does_not_lock_on_its_own(self):
        self.fail_once()
        self.assertEqual(auth.lockout_remaining("cashier"), 0)
        self.assertTrue(auth.authenticate("cashier", "correct-horse"))

    def test_the_account_locks_on_the_configured_attempt(self):
        self.fail_once()
        self.fail_once()
        with self.assertRaises(auth.AccountLocked):
            auth.authenticate("cashier", "wrong")
        self.assertGreater(auth.lockout_remaining("cashier"), 0)

    def test_a_locked_account_refuses_even_the_right_password(self):
        for _ in range(3):
            self.fail_once()
        with self.assertRaises(auth.AccountLocked) as caught:
            auth.authenticate("cashier", "correct-horse")
        self.assertGreater(caught.exception.seconds_remaining, 0)

    def test_a_successful_sign_in_forgets_earlier_failures(self):
        self.fail_once()
        self.fail_once()
        auth.authenticate("cashier", "correct-horse")
        self.fail_once()
        self.fail_once()
        # Two fresh failures, not four cumulative ones, so still not locked.
        self.assertEqual(auth.lockout_remaining("cashier"), 0)

    def test_the_lock_expires_on_its_own(self):
        for _ in range(3):
            self.fail_once()
        past = (dt.datetime.now() - dt.timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
        db.execute(
            "UPDATE login_throttle SET locked_until = ? WHERE username = 'cashier'",
            (past,),
        )
        self.assertEqual(auth.lockout_remaining("cashier"), 0)
        self.assertTrue(auth.authenticate("cashier", "correct-horse"))

    def test_an_administrator_can_unlock_an_account(self):
        for _ in range(3):
            self.fail_once()
        self.assertEqual([row["username"] for row in auth.locked_accounts()], ["cashier"])
        auth.clear_lockout("cashier")
        self.assertEqual(auth.lockout_remaining("cashier"), 0)
        self.assertEqual(auth.locked_accounts(), [])

    def test_resetting_the_password_also_unlocks(self):
        for _ in range(3):
            self.fail_once()
        auth.set_password(self.user_id, "brand-new-one", must_change=True)
        self.assertEqual(auth.lockout_remaining("cashier"), 0)

    def test_locking_one_account_does_not_lock_another(self):
        auth.create_user("other", "another-one", config.ROLE_EMPLOYEE)
        for _ in range(3):
            self.fail_once()
        self.assertTrue(auth.authenticate("other", "another-one"))

    def test_throttling_can_be_switched_off(self):
        settings_service.set_value("login_max_attempts", "0")
        for _ in range(6):
            self.fail_once()
        self.assertEqual(auth.lockout_remaining("cashier"), 0)
        self.assertTrue(auth.authenticate("cashier", "correct-horse"))

    def test_an_unknown_username_is_throttled_too(self):
        # Otherwise the lockout tells an attacker which usernames are real.
        for _ in range(3):
            with self.assertRaises(auth.AuthError):
                auth.authenticate("ghost", "guess")
        self.assertGreater(auth.lockout_remaining("ghost"), 0)

    def test_a_lockout_is_written_to_the_audit_log(self):
        for _ in range(3):
            self.fail_once()
        actions = [row["action"] for row in audit_service.list_entries()]
        self.assertIn("Account locked", actions)


class HashUpgradeTests(DatabaseTestCase):
    opens_shift = False

    def test_a_password_hashed_at_a_lower_cost_is_upgraded_on_use(self):
        user_id = auth.create_user("legacy", "old-password", config.ROLE_EMPLOYEE)
        db.execute(
            "UPDATE users SET password_hash = ? WHERE user_id = ?",
            (auth.hash_password("old-password", iterations=1000), user_id),
        )

        auth.authenticate("legacy", "old-password")

        stored = db.scalar("SELECT password_hash FROM users WHERE user_id = ?", (user_id,))
        self.assertEqual(int(stored.split("$")[1]), auth.ITERATIONS)
        self.assertTrue(auth.verify_password("old-password", stored))

    def test_a_current_hash_is_left_alone(self):
        user_id = auth.create_user("modern", "good-password", config.ROLE_EMPLOYEE)
        before = db.scalar("SELECT password_hash FROM users WHERE user_id = ?", (user_id,))
        auth.authenticate("modern", "good-password")
        after = db.scalar("SELECT password_hash FROM users WHERE user_id = ?", (user_id,))
        self.assertEqual(before, after)

    def test_a_wrong_password_never_rewrites_the_hash(self):
        user_id = auth.create_user("legacy", "old-password", config.ROLE_EMPLOYEE)
        weak = auth.hash_password("old-password", iterations=1000)
        db.execute(
            "UPDATE users SET password_hash = ? WHERE user_id = ?", (weak, user_id)
        )
        with self.assertRaises(auth.AuthError):
            auth.authenticate("legacy", "not-the-password")
        self.assertEqual(
            db.scalar("SELECT password_hash FROM users WHERE user_id = ?", (user_id,)),
            weak,
        )


class ForcedChangeTests(DatabaseTestCase):
    opens_shift = False

    def test_the_bootstrap_admin_must_choose_a_real_password(self):
        self.assertTrue(auth.authenticate("admin", "admin123").must_change_password)

    def test_changing_it_clears_the_flag(self):
        auth.change_password(self.admin.user_id, "admin123", "a-proper-password")
        self.assertFalse(auth.authenticate("admin", "a-proper-password").must_change_password)

    def test_an_admin_reset_forces_the_owner_to_choose_a_new_one(self):
        user_id = auth.create_user("cashier", "first-password", config.ROLE_EMPLOYEE)
        self.assertFalse(auth.authenticate("cashier", "first-password").must_change_password)

        auth.set_password(user_id, "temporary-one", must_change=True)
        self.assertTrue(auth.authenticate("cashier", "temporary-one").must_change_password)

        auth.change_password(user_id, "temporary-one", "their-own-choice")
        self.assertFalse(auth.authenticate("cashier", "their-own-choice").must_change_password)

    def test_a_new_password_must_differ_from_the_old_one(self):
        with self.assertRaises(auth.AuthError):
            auth.change_password(self.admin.user_id, "admin123", "admin123")

    def test_signing_in_records_the_time(self):
        auth.authenticate("admin", "admin123")
        self.assertIsNotNone(
            db.scalar(
                "SELECT last_login_at FROM users WHERE user_id = ?", (self.admin.user_id,)
            )
        )
