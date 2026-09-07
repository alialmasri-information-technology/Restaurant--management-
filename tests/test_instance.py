"""One copy of the register per shop: the single-instance guard.

The refusal itself is the part worth testing, and it can be: the named mutex
is a property of the machine, not of the process, so clearing the module's
handle -- which is all a second copy of RE4 starts with -- is enough to make
acquire() take the path a real second copy takes. It needs no display and no
second process, which is what this file used to say it needed.

One thing these tests do assume: that no real copy of RE4 is running on this
machine. It holds the same mutex, so it would turn the first acquire() here
away exactly as intended.
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
    def test_a_second_copy_is_turned_away(self):
        """The branch the whole module exists for, and the one never covered.

        A second copy of RE4 starts with no handle of its own and finds the
        mutex already made. Dropping the handle here without closing it leaves
        the machine in exactly that state.
        """
        self.assertTrue(instance.acquire())
        held = instance._mutex
        self.addCleanup(setattr, instance, "_mutex", held)
        instance._mutex = None
        try:
            self.assertFalse(
                instance.acquire(),
                "a second copy was let in, which is two tills on one database",
            )
        finally:
            instance._mutex = held

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
