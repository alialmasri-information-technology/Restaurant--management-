"""One version number, written down in four places, all of which must agree.

The application reports `APP_VERSION`, the installer names its output file
after `AppVersion`, the package metadata carries its own, and the changelog is
what a shop reads to find out what changed. Nothing enforces the connection at
runtime: a release with the exe saying 2.7.1 and the installer producing
RE4-Setup-2.7.0.exe would build, pass, publish, and only be noticed by whoever
downloaded the wrong-looking file.

installer.iss says "Bump this with each release, alongside pyproject.toml and
app/config.py" in a comment. That comment is a request; this is the check.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from app import config

ROOT = Path(__file__).resolve().parent.parent


def _read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def _one(pattern: str, text: str, where: str) -> str:
    found = re.findall(pattern, text, re.MULTILINE)
    if len(found) != 1:
        raise AssertionError(
            f"{where}: expected exactly one match for {pattern!r}, found {len(found)}"
        )
    return found[0]


class VersionTests(unittest.TestCase):
    def test_the_package_metadata_agrees_with_the_application(self):
        # Parsed with a regex rather than tomllib, which arrived in 3.11 and
        # this project still supports 3.10.
        declared = _one(r'^version = "([^"]+)"$', _read("pyproject.toml"), "pyproject.toml")
        self.assertEqual(declared, config.APP_VERSION)

    def test_the_installer_agrees_with_the_application(self):
        declared = _one(
            r'^#define AppVersion "([^"]+)"$', _read("installer.iss"), "installer.iss"
        )
        self.assertEqual(
            declared,
            config.APP_VERSION,
            "the installer would be named after a version the application does not report",
        )

    def test_the_changelog_has_an_entry_for_this_version(self):
        headings = re.findall(r"^## \[([^\]]+)\]", _read("CHANGELOG.md"), re.MULTILINE)
        self.assertIn(
            config.APP_VERSION,
            headings,
            "a release with nothing said about it in the changelog is a release "
            "nobody can find out about",
        )

    def test_the_changelog_leads_with_this_version(self):
        """Newest first, so the entry being added is the one at the top."""
        headings = re.findall(r"^## \[([^\]]+)\]", _read("CHANGELOG.md"), re.MULTILINE)
        self.assertEqual(headings[0], config.APP_VERSION)

    def test_the_build_reads_the_version_rather_than_repeating_it(self):
        """A fifth copy in RE4.spec would be a fifth thing to forget.

        The spec pulls APP_VERSION out of app/config.py at build time, so the
        version resource stamped on the exe cannot disagree with what the exe
        reports when asked. This guards the arrangement, not the wording.
        """
        spec = _read("RE4.spec")
        self.assertIn("APP_VERSION", spec)
        # Naming the offending lines rather than letting assertNotIn print the
        # whole spec back, which buries the one line that matters.
        written_in = [
            f"  RE4.spec:{number}: {line.strip()}"
            for number, line in enumerate(spec.splitlines(), start=1)
            if config.APP_VERSION in line
        ]
        self.assertEqual(
            written_in,
            [],
            "RE4.spec has the version written into it; it should read it from "
            "app/config.py instead:\n" + "\n".join(written_in),
        )


if __name__ == "__main__":
    unittest.main()
