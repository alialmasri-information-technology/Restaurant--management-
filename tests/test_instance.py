"""One copy of the register per shop: the single-instance guard.

The tests can only exercise what one process can see: that acquiring twice in
this process is the same as holding the door once, that the guard never
refuses a first run, and that giving it up lets it be taken again. The real
second-instance refusal needs two processes and a display, which is a job for
the person at the till, not the suite.
"""

from __future__ import annotations

import sys
import unittest

from app import instance
from tests.support import DatabaseTestCase


class SingleInstanceTests(DatabaseTestCase):
    opens_shift = False

    def tearDown(self) -> None:
        instance.release()
        super().tearDown()

    @unittest.skipUnless(sys.platform.startswith("win"), "Windows mutex semantics")
    def test_the_first_process_acquires(self):
        self.assertTrue(instance.acquire())

    @unittest.skipUnless(sys.platform.startswith("win"), "Windows mutex semantics")
    def test_reacquiring_in_one_process_holds_one_door(self):
        self.assertTrue(instance.acquire())
        self.assertTrue(instance.acquire())

    @unittest.skipUnless(sys.platform.startswith("win"), "Windows mutex semantics")
    def test_release_lets_the_door_be_taken_again(self):
        self.assertTrue(instance.acquire())
        instance.release()
        self.assertTrue(instance.acquire())


if not sys.platform.startswith("win"):

    class LockFileTests(DatabaseTestCase):
        """The advisory-lock path every non-Windows platform takes."""

        opens_shift = False

        def tearDown(self) -> None:
            instance.release()
            super().tearDown()

        def test_the_first_process_acquires(self):
            self.assertTrue(instance.acquire())

        def test_release_lets_the_door_be_taken_again(self):
            self.assertTrue(instance.acquire())
            instance.release()
            self.assertTrue(instance.acquire())
