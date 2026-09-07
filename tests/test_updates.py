"""The walkthrough asks once, and the update banner asks politely."""

from __future__ import annotations

import http.client
import io
import os
import unittest
import urllib.error
import urllib.request
from unittest import mock

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

    def setUp(self) -> None:
        super().setUp()
        # The suite-wide switch turns the check off so no screen built in a
        # test can phone home; these tests exercise the check itself, so they
        # take the switch off and put it back.
        self._original_switch = os.environ.pop(updates.TEST_SWITCH, None)

    def tearDown(self) -> None:
        if self._original_switch is not None:
            os.environ[updates.TEST_SWITCH] = self._original_switch
        else:
            os.environ.pop(updates.TEST_SWITCH, None)
        super().tearDown()

    def test_the_switch_silences_the_check_entirely(self):
        os.environ[updates.TEST_SWITCH] = "1"
        self.assertIsNone(updates.check(settings_service))

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


class UnreachableNetworkTests(unittest.TestCase):
    """Every way a network can answer badly, and none of them a dialog.

    Being offline was the only case handled, and it is the easy one -- the
    request raises URLError and nothing else happens. The awkward case is a
    connection that answers, but not with a release: the captive portal in a
    mall, a shared building or a cafe, which is exactly where a small shop's
    till sits. Those raise from http.client, which is neither OSError nor
    ValueError, so they escaped.

    Escaping matters because the shell asks this question on the background
    worker, and a job that raises with no on_error of its own gets the default
    handler: an error dialog. A version check nobody asked for became a modal
    in front of whoever opened the till that morning.
    """

    def answer(self, body: bytes):
        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *exc_info):
                return False

        return lambda *a, **k: Response(body)

    def raises(self, exc):
        def fake(*a, **k):
            raise exc
        return fake

    def check(self, fake_urlopen):
        with mock.patch.object(urllib.request, "urlopen", fake_urlopen):
            return updates.latest_release()

    def test_a_connection_that_answers_badly_is_as_quiet_as_no_connection(self):
        for label, fake in (
            ("truncated body", self.raises(http.client.IncompleteRead(b"{"))),
            ("malformed status line", self.raises(http.client.BadStatusLine("<html>"))),
            ("oversized header", self.raises(http.client.LineTooLong("status line"))),
            ("no route at all", self.raises(urllib.error.URLError("offline"))),
            ("a login page", self.answer(b"<html>Sign in to the network</html>")),
        ):
            with self.subTest(label=label):
                self.assertIsNone(self.check(fake))

    def test_json_that_is_not_a_release_is_not_asked_for_a_tag(self):
        """A portal can answer with well-formed JSON of the wrong shape."""
        for body in (b"[]", b'"login required"', b"null", b"42"):
            with self.subTest(body=body):
                self.assertIsNone(self.check(self.answer(body)))

    def test_a_real_release_still_gets_through(self):
        """The quieting must not have silenced the answer as well."""
        found = self.check(self.answer(
            b'{"tag_name": "v99.0.0", "html_url": "https://example.com/r"}'
        ))
        self.assertEqual(found, ("99.0.0", "https://example.com/r"))

    def test_a_release_that_is_not_newer_is_no_answer(self):
        found = self.check(self.answer(
            b'{"tag_name": "v0.0.1", "html_url": "https://example.com/r"}'
        ))
        self.assertIsNone(found)

    def test_a_release_with_no_page_is_no_answer(self):
        found = self.check(self.answer(b'{"tag_name": "v99.0.0", "html_url": ""}'))
        self.assertIsNone(found)


class BannerFailurePathTests(unittest.TestCase):
    """The shell must not rely on the module above never raising."""

    def source_of(self, name: str) -> str:
        import inspect

        from app.ui import shell

        return inspect.getsource(getattr(shell.AppShell, name))

    def test_the_shell_hands_the_check_its_own_error_handler(self):
        """Otherwise background.run supplies one, and its default is a dialog."""
        self.assertIn("_update_check_failed", self.source_of("_check_for_update"))

    def test_that_handler_logs_rather_than_showing_anything(self):
        source = self.source_of("_update_check_failed")
        self.assertIn("logs.", source)
        self.assertNotIn("show_error", source)
