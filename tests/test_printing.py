"""Handing a receipt to a printer.

Nothing here prints. What is checked is the shape of the request the module
makes of the operating system, because that is where this module was wrong:
it asked PowerShell to print, in a way PowerShell does not support, and the
failure was invisible -- the job went to the default printer and the status
line said it had been sent.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from app import printing

MODULE_SOURCE = Path(printing.__file__).read_text(encoding="utf-8")


class WindowsPrintTests(unittest.TestCase):
    """The Windows path, driven on any platform by standing in for the shell."""

    def setUp(self):
        self.pdf = Path(self.enterContext(_temp_pdf()))
        self.enterContext(mock.patch.object(printing, "IS_WINDOWS", True))
        self.startfile = self.enterContext(
            mock.patch.object(printing.os, "startfile", create=True)
        )
        self.run = self.enterContext(mock.patch.object(printing, "_run"))

    def test_the_chosen_printer_is_actually_asked_for(self):
        """The bug this file exists for: it never was.

        The old code passed the file and the printer to powershell.exe as
        $args[0] and $args[1]. -Command has no $args -- it joins the trailing
        words onto the command text -- so Start-Process was given a null
        FilePath, failed every time, and the job fell through to the default
        printer with "Sent to Front Counter." returned to the user.
        """
        status = printing.print_file(self.pdf, "Front Counter")

        self.startfile.assert_called_once_with(
            str(self.pdf), "printto", '"Front Counter"'
        )
        self.assertEqual(status, "Sent to Front Counter.")

    def test_no_shell_is_involved_in_a_print_job(self):
        """A printer's name reached a command line that was then parsed."""
        printing.print_file(self.pdf, "Front Counter")
        self.run.assert_not_called()

    def test_a_name_that_would_be_parsed_as_a_command_is_only_a_name(self):
        printing.print_file(self.pdf, '; Remove-Item C:/')
        argument = self.startfile.call_args.args[2]
        self.assertEqual(argument, '"; Remove-Item C:/"')

    def test_a_printer_that_cannot_be_targeted_falls_back_and_says_so(self):
        self.startfile.side_effect = [OSError("no PrintTo verb"), None]
        status = printing.print_file(self.pdf, "Front Counter")

        self.assertEqual(self.startfile.call_count, 2)
        self.assertEqual(self.startfile.call_args.args[:2], (str(self.pdf), "print"))
        self.assertIn("default printer", status)
        self.assertIn("Front Counter", status)

    def test_no_printer_chosen_goes_straight_to_the_default(self):
        status = printing.print_file(self.pdf, "")
        self.startfile.assert_called_once_with(str(self.pdf), "print")
        self.assertEqual(status, "Sent to the default printer.")

    def test_nothing_to_print_is_refused_before_the_shell_is_touched(self):
        with self.assertRaises(printing.PrintError):
            printing.print_file(self.pdf.parent / "not-there.pdf", "")
        self.startfile.assert_not_called()

    def test_the_default_printer_failing_is_reported_not_swallowed(self):
        self.startfile.side_effect = OSError("no PDF reader")
        with self.assertRaises(printing.PrintError) as caught:
            printing.print_file(self.pdf, "")
        self.assertIn("PDF reader", str(caught.exception))


class ShellArgumentTests(unittest.TestCase):
    def test_a_name_with_a_space_arrives_as_one_argument(self):
        """Most printer names have one, and the reader splits the line itself."""
        self.assertEqual(printing._shell_argument("Front Counter"), '"Front Counter"')

    def test_a_quote_in_the_name_cannot_end_the_quoting_early(self):
        self.assertEqual(printing._shell_argument('Back" Office'), '"Back Office"')

    def test_an_empty_name_stays_empty(self):
        self.assertEqual(printing._shell_argument(""), '""')


class UnixPrintTests(unittest.TestCase):
    def setUp(self):
        self.pdf = Path(self.enterContext(_temp_pdf()))
        self.enterContext(mock.patch.object(printing, "IS_WINDOWS", False))
        self.run = self.enterContext(mock.patch.object(printing, "_run"))
        self.run.return_value = mock.Mock(returncode=0, stdout="", stderr="")

    def test_lpr_is_given_a_list_so_the_name_is_never_parsed(self):
        printing.print_file(self.pdf, "; rm -rf /")
        command = self.run.call_args.args[0]
        self.assertEqual(command, ["lpr", "-P", "; rm -rf /", str(self.pdf)])

    def test_a_failure_is_raised_rather_than_reported_as_success(self):
        self.run.return_value = mock.Mock(returncode=1, stdout="", stderr="no printer")
        with self.assertRaises(printing.PrintError) as caught:
            printing.print_file(self.pdf, "")
        self.assertIn("no printer", str(caught.exception))


class SourceTests(unittest.TestCase):
    """The specific mistake, kept out by name."""

    def test_nothing_passes_values_to_powershell_as_dollar_args(self):
        self.assertNotIn(
            "$args", MODULE_SOURCE,
            "powershell.exe -Command has no $args: it joins the trailing words "
            "onto the command text and parses the result, so the values arrive "
            "as null and as code",
        )

    def test_the_powershell_that_remains_interpolates_nothing(self):
        """Listing printers still uses it, and may keep doing so."""
        for constant in (printing._LIST_PRINTERS_PS, printing._DEFAULT_PRINTER_PS):
            with self.subTest(constant=constant):
                self.assertNotIn("{", constant)
                self.assertNotIn("%s", constant)


def _temp_pdf():
    import contextlib
    import tempfile

    @contextlib.contextmanager
    def opened():
        directory = tempfile.TemporaryDirectory()
        try:
            path = Path(directory.name) / "receipt.pdf"
            path.write_bytes(b"%PDF-1.4\n")
            yield path
        finally:
            directory.cleanup()

    return opened()


if __name__ == "__main__":
    unittest.main()
