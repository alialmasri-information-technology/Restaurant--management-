"""The text the shop reads must survive being edited.

An em dash, a middle dot and an ellipsis are ordinary punctuation in this
application: they separate the figures on a summary line, and they end a search
placeholder. They are also the first thing to break when a file makes a round
trip through a tool that guesses the encoding wrongly, and what comes back is
U+FFFD -- the replacement character, the black diamond with a question mark in
it. Nothing raises, no test fails, and the shop is left looking at

    12 card(s) <?> 9 active <?> the shop owes $340.00 on cards

on a screen it uses every day. Seven of those shipped in v2.7.0 before anyone
noticed, which is the whole argument for checking mechanically.
"""

from __future__ import annotations

import unittest
from pathlib import Path

#: The character an encoding accident leaves behind. Never intentional here.
REPLACEMENT = "\ufffd"

ROOT = Path(__file__).resolve().parent.parent
SEARCHED = ("*.py", "*.md", "*.iss", "*.toml", "*.yml", "*.bat")
SKIPPED = {".git", ".venv", "venv", "build", "dist", "__pycache__", ".ruff_cache"}


def _source_files():
    for pattern in SEARCHED:
        for path in ROOT.rglob(pattern):
            if not SKIPPED.isdisjoint(path.relative_to(ROOT).parts):
                continue
            yield path


class EncodingTests(unittest.TestCase):
    def test_no_source_file_carries_a_replacement_character(self):
        damaged = []
        for path in _source_files():
            text = path.read_text(encoding="utf-8", errors="replace")
            for number, line in enumerate(text.splitlines(), start=1):
                if REPLACEMENT in line:
                    damaged.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()}")

        self.assertEqual(
            damaged,
            [],
            "Punctuation was lost to a bad encoding round trip. Put the "
            "intended character back. These are named rather than printed, "
            "because a console that cannot show them is how the damage "
            "happens in the first place: U+2026 HORIZONTAL ELLIPSIS ends a "
            "search placeholder, U+00B7 MIDDLE DOT separates figures on one "
            "line, U+2014 EM DASH stands in for a cell with nothing in "
            "it.\n  " + "\n  ".join(damaged),
        )

    def test_every_source_file_is_readable_as_utf8(self):
        """errors="replace" above would hide a file that is not UTF-8 at all."""
        for path in _source_files():
            with self.subTest(path=str(path.relative_to(ROOT))):
                try:
                    path.read_bytes().decode("utf-8")
                except UnicodeDecodeError as exc:
                    self.fail(f"{path.relative_to(ROOT)} is not UTF-8: {exc}")


if __name__ == "__main__":
    unittest.main()
