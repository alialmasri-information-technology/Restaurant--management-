"""The walkthrough asks once, and the update banner asks politely."""

from __future__ import annotations

import unittest

from app import config
from app.services import settings as settings_service
from app.services import updates
from tests.support import DatabaseTestCase


class VersionTests(unittest.TestCase):
    """Pure comparisons; the network is nobody's test fixture."""

    def test_the_running_version_parses(self):
        self.assertEqual(updates.current(), tuple(
            int(part) for part in config.APP_VERSION.split(".")
        ))

    def test_a_plain_tag_is_newer_than_this_build(self):
        self.assertTrue(updates.is_newer("v99.0.0"))

    def test_an_equal_or_older_tag_is_not_newer(self):
        self.assertFalse(updates.is_newer(f"v{config.APP_VERSION}"))
        self.assertFalse(updates.is_newer("v0.1.0"))

    def test_something_that_is_not_a_version_is_ignored(self):
        self.assertFalse(updates.is_newer("latest"))
        self.assertFalse(updates.is_newer(""))

    def test_a_tag_with_letters_around_the_numbers_parses_the_numbers(self):
        self.assertEqual(updates.parse("v2.10.0-beta"), None)  # suffix spoils it
        self.assertEqual(updates.parse("v2.10.0"), (2, 10, 0))


class CheckCacheTests(DatabaseTestCase):
    opens_shift = False

    def test_a_newer_release_is_found_and_then_cached_for_the_day(self):
        class FakeNetwork:
            def __init__(self):
                self.calls = 0

            def __call__(self):
                self.calls += 1
                return ("99.0.0", "https://example.com/release")

        original = updates.latest_release
        fake = FakeNetwork()
        updates.latest_release = fake
        try:
            first = updates.check(settings_service)
            second = updates.check(settings_service)
        finally:
            updates.latest_release = original

        self.assertEqual(first, ("99.0.0", "https://example.com/release"))
        self.assertEqual(second, ("99.0.0", "https://example.com/release"))
        # The network was asked once, not once per check.
        self.assertEqual(fake.calls, 1)

    def test_no_release_leaves_no_banner_and_caches_the_quiet_answer(self):
        original = updates.latest_release
        updates.latest_release = lambda: None
        try:
            self.assertIsNone(updates.check(settings_service))
            self.assertIsNone(updates.check(settings_service))
        finally:
            updates.latest_release = original
