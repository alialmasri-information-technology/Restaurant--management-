"""Generating, opening, saving and printing PDF documents from the UI."""

from __future__ import annotations

import os
import subprocess
import sys
import webbrowser
from pathlib import Path
from tkinter import filedialog

from app import printing, receipts
from app.services import sales as sales_service
from app.services import settings as settings_service
from app.ui.widgets import ask_confirm, show_error, show_info


def open_file(path: Path) -> None:
    """Hand the file to whatever the OS uses to view PDFs."""
    path = Path(path)
    try:
        if sys.platform.startswith("win"):
            # Our own generated PDF, never a path typed by anyone.
            os.startfile(str(path))
        elif sys.platform == "darwin":
            subprocess.run(["open", str(path)], check=False)
        else:
            subprocess.run(["xdg-open", str(path)], check=False)
    except Exception:  # noqa: BLE001 - fall back to the browser's PDF viewer
        webbrowser.open(path.as_uri())


def send_to_printer(parent, path: Path, quiet: bool = False) -> bool:
    """Print a generated file, reporting what happened."""
    try:
        status = printing.print_file(path, settings_service.printer_name())
    except printing.PrintError as exc:
        show_error(
            parent,
            f"{exc}\n\nThe document is saved at:\n{path}",
            "Could not print",
        )
        return False
    if not quiet:
        show_info(parent, status, "Sent to printer")
    return True


def _produce(parent, make, failure: str):
    """Run a generator, reporting failures rather than raising into Tk."""
    try:
        return make()
    except Exception as exc:  # noqa: BLE001 - reportlab or disk problems
        show_error(parent, exc, failure)
        return None


# --------------------------------------------------------------------------- #
# Sale receipts
# --------------------------------------------------------------------------- #

def print_receipt(parent, sale_id: int, *, open_after: bool = True) -> Path | None:
    """Write the receipt to the app's receipts folder and open it."""
    path = _produce(
        parent, lambda: receipts.generate_receipt(sale_id),
        "Could not create the receipt",
    )
    if path and open_after:
        open_file(path)
    return path


def print_receipt_direct(parent, sale_id: int, quiet: bool = False) -> Path | None:
    """Send the receipt straight to the configured printer."""
    path = _produce(
        parent, lambda: receipts.generate_receipt(sale_id),
        "Could not create the receipt",
    )
    if path:
        send_to_printer(parent, path, quiet=quiet)
    return path


def save_receipt_as(parent, sale_id: int) -> Path | None:
    """Ask where to put the PDF, then write it there."""
    sale = sales_service.get_sale(sale_id)
    if sale is None:
        show_error(parent, "Sale not found.", "Cannot save receipt")
        return None

    target = filedialog.asksaveasfilename(
        parent=parent,
        title="Save receipt as",
        defaultextension=".pdf",
        initialfile=f"{sale['invoice_no']}.pdf",
        filetypes=[("PDF document", "*.pdf")],
    )
    if not target:
        return None
    path = _produce(
        parent, lambda: receipts.generate_receipt(sale_id, target),
        "Could not create the receipt",
    )
    if path:
        show_info(parent, f"Receipt saved to:\n{path}", "Receipt saved")
    return path


def offer_receipt(parent, sale_id: int, message: str) -> None:
    """Confirm a completed sale, printing or opening the receipt as configured."""
    if settings_service.printer_name():
        print_receipt_direct(parent, sale_id, quiet=True)
        show_info(parent, message, "Sale completed")
        return
    if ask_confirm(parent, f"{message}\n\nOpen the PDF receipt now?", "Sale completed"):
        print_receipt(parent, sale_id)


# --------------------------------------------------------------------------- #
# Return slips and till reports
# --------------------------------------------------------------------------- #

def print_return_slip(parent, return_id: int, *, open_after: bool = True) -> Path | None:
    path = _produce(
        parent, lambda: receipts.generate_return_receipt(return_id),
        "Could not create the return slip",
    )
    if not path:
        return None
    if settings_service.printer_name():
        send_to_printer(parent, path, quiet=True)
    elif open_after:
        open_file(path)
    return path


def print_shift_report(parent, shift_id: int, kind: str = "Z") -> Path | None:
    path = _produce(
        parent, lambda: receipts.generate_shift_report(shift_id, kind=kind),
        f"Could not create the {kind} report",
    )
    if not path:
        return None
    if settings_service.printer_name():
        send_to_printer(parent, path, quiet=True)
    else:
        open_file(path)
    return path
