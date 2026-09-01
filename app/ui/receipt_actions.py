"""Generating, opening and saving PDF receipts from the UI."""

from __future__ import annotations

import os
import subprocess
import sys
import webbrowser
from pathlib import Path
from tkinter import filedialog

from app import receipts
from app.services import sales as sales_service
from app.ui.widgets import ask_confirm, show_error, show_info


def open_file(path: Path) -> None:
    """Hand the file to whatever the OS uses to view PDFs."""
    path = Path(path)
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(path))  # noqa: S606 - opening our own generated file
        elif sys.platform == "darwin":
            subprocess.run(["open", str(path)], check=False)
        else:
            subprocess.run(["xdg-open", str(path)], check=False)
    except Exception:  # noqa: BLE001 - fall back to the browser's PDF viewer
        webbrowser.open(path.as_uri())


def print_receipt(parent, sale_id: int, *, open_after: bool = True) -> Path | None:
    """Write the receipt to the app's receipts folder and open it."""
    try:
        path = receipts.generate_receipt(sale_id)
    except Exception as exc:  # noqa: BLE001 - reportlab or disk problems
        show_error(parent, exc, "Could not create the receipt")
        return None
    if open_after:
        open_file(path)
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
    try:
        path = receipts.generate_receipt(sale_id, target)
    except Exception as exc:  # noqa: BLE001
        show_error(parent, exc, "Could not create the receipt")
        return None
    show_info(parent, f"Receipt saved to:\n{path}", "Receipt saved")
    return path


def offer_receipt(parent, sale_id: int, message: str) -> None:
    """Confirm a completed sale and offer to open its receipt."""
    if ask_confirm(
        parent,
        f"{message}\n\nOpen the PDF receipt now?",
        "Sale completed",
    ):
        print_receipt(parent, sale_id)
