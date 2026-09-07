"""Sending a generated PDF to a physical printer.

There is no cross-platform printing API in the standard library, so this uses
the shell verbs Windows registers for PDF handlers and ``lpr`` elsewhere.
Everything degrades: if a named printer cannot be targeted the job falls back to
the system default, and if that fails too the caller can still open the PDF and
print it by hand.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from app import logs

IS_WINDOWS = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"

# PowerShell is only asked for a plain list of names; nothing is interpolated.
_LIST_PRINTERS_PS = "Get-Printer | Select-Object -ExpandProperty Name"
_DEFAULT_PRINTER_PS = (
    "(Get-CimInstance -Class Win32_Printer | Where-Object Default -eq $true).Name"
)


class PrintError(Exception):
    """Raised when a print job could not be handed to the operating system."""


def _run(command, timeout: int = 20) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
        # Keep a console window from flashing up in the frozen, windowed build.
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if IS_WINDOWS else 0,
    )


def list_printers() -> list[str]:
    """Installed printer names. Returns an empty list if they cannot be listed."""
    try:
        if IS_WINDOWS:
            result = _run(["powershell", "-NoProfile", "-Command", _LIST_PRINTERS_PS])
        else:
            result = _run(["lpstat", "-a"])
    except (OSError, subprocess.SubprocessError):
        logs.warning("Could not list printers", exc_info=False)
        return []

    if result.returncode != 0:
        return []
    names = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        names.append(line if IS_WINDOWS else line.split()[0])
    return names


def default_printer() -> str:
    try:
        if IS_WINDOWS:
            result = _run(["powershell", "-NoProfile", "-Command", _DEFAULT_PRINTER_PS])
        else:
            result = _run(["lpstat", "-d"])
    except (OSError, subprocess.SubprocessError):
        # Its sibling above logs the same failure; a shop with no default
        # printer set is a normal state, but the command falling over is not.
        logs.warning("Could not ask the system for the default printer", exc_info=False)
        return ""
    if result.returncode != 0:
        return ""
    text = result.stdout.strip()
    if not IS_WINDOWS and ":" in text:
        text = text.split(":", 1)[1].strip()
    return text


def print_file(path, printer_name: str = "") -> str:
    """Send ``path`` to ``printer_name`` (or the default). Returns a status line."""
    path = Path(path)
    if not path.exists():
        raise PrintError(f"Nothing to print — {path.name} was not found.")

    printer_name = (printer_name or "").strip()
    try:
        if IS_WINDOWS:
            return _print_windows(path, printer_name)
        if IS_MAC or not IS_WINDOWS:
            return _print_unix(path, printer_name)
    except PrintError:
        raise
    except Exception as exc:  # whatever the platform threw, the user sees it
        logs.exception("Printing failed for %s", path)
        raise PrintError(f"The printer could not be reached: {exc}") from exc
    raise PrintError("Printing is not supported on this platform.")


def _print_windows(path: Path, printer_name: str) -> str:
    if printer_name:
        # "PrintTo" is registered by every mainstream PDF reader.
        command = [
            "powershell", "-NoProfile", "-Command",
            "Start-Process -FilePath $args[0] -Verb PrintTo -ArgumentList $args[1]",
            str(path), printer_name,
        ]
        result = _run(command, timeout=30)
        if result.returncode == 0:
            return f"Sent to {printer_name}."
        logs.warning(
            "PrintTo failed for %s on %s: %s", path, printer_name,
            (result.stderr or "").strip(),
        )

    try:
        # The path is a PDF this application generated, never user input.
        os.startfile(str(path), "print")
    except OSError as exc:
        raise PrintError(
            "Windows could not print the file. Check that a PDF reader is "
            "installed and set as the default for .pdf files."
        ) from exc
    return "Sent to the default printer." if not printer_name else (
        f"'{printer_name}' could not be targeted directly, so the job went to the "
        f"default printer."
    )


def _print_unix(path: Path, printer_name: str) -> str:
    command = ["lpr"]
    if printer_name:
        command += ["-P", printer_name]
    command.append(str(path))
    try:
        result = _run(command, timeout=30)
    except FileNotFoundError as exc:
        raise PrintError("'lpr' is not installed, so the file could not be printed.") from exc
    if result.returncode != 0:
        raise PrintError((result.stderr or "The print command failed.").strip())
    return f"Sent to {printer_name}." if printer_name else "Sent to the default printer."
