"""Application-wide paths and constants.

The important subtlety here is where the database lives once the app is frozen
by PyInstaller. ``sys._MEIPASS`` points at a temporary extraction directory that
is wiped when the process exits, so writing the database there would silently
throw away every sale. Data therefore always lives next to the executable (or
next to the project root in development), never inside the bundle.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "RE4"
APP_TITLE = "RE4 — Business & Retail Management"
APP_VERSION = "2.2.0"

IS_FROZEN = getattr(sys, "frozen", False)


def _bundle_dir() -> Path:
    """Read-only directory holding bundled resources (images, templates)."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return Path(__file__).resolve().parent.parent


def _data_dir() -> Path:
    """Writable directory for the database, receipts, backups and logs."""
    override = os.environ.get("RE4_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if IS_FROZEN:
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


BUNDLE_DIR = _bundle_dir()
DATA_DIR = _data_dir()
DATABASE_PATH = DATA_DIR / "re4.db"
RECEIPTS_DIR = DATA_DIR / "receipts"
BACKUPS_DIR = DATA_DIR / "backups"
IMAGES_DIR = DATA_DIR / "images"
LOGS_DIR = DATA_DIR / "logs"

ROLE_ADMIN = "Admin"
ROLE_EMPLOYEE = "Employee"
ROLES = (ROLE_ADMIN, ROLE_EMPLOYEE)

PAYMENT_METHODS = ("Cash", "Card", "Bank Transfer", "Credit")
CURRENCIES = ("USD", "LBP")

SALE_COMPLETED = "Completed"
SALE_REFUNDED = "Refunded"

SHIFT_OPEN = "Open"
SHIFT_CLOSED = "Closed"

CASH_IN = "In"
CASH_OUT = "Out"
CASH_REASONS_IN = ("Float top-up", "Owner deposit", "Correction", "Other")
CASH_REASONS_OUT = ("Supplier payment", "Petty cash", "Bank drop", "Correction", "Other")

PO_DRAFT = "Draft"
PO_ORDERED = "Ordered"
PO_PARTIAL = "Partially Received"
PO_RECEIVED = "Received"
PO_CANCELLED = "Cancelled"
PO_STATUSES = (PO_DRAFT, PO_ORDERED, PO_PARTIAL, PO_RECEIVED, PO_CANCELLED)

RETURN_REASONS = ("Faulty", "Wrong item", "Customer changed mind", "Damaged", "Other")

TAKE_OPEN = "Open"
TAKE_APPLIED = "Applied"
TAKE_CANCELLED = "Cancelled"
TAKE_STATUSES = (TAKE_OPEN, TAKE_APPLIED, TAKE_CANCELLED)

STOCK_REASONS = (
    "Initial",
    "Restock",
    "Sale",
    "Return",
    "Purchase",
    "Adjustment",
    "Spoilage",
    "Import",
    "Stock take",
)

RECEIPT_WIDTHS = ("58", "80")
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp")

# Seeded on first run; every one of these is editable from Settings.
DEFAULT_SETTINGS = {
    "store_name": "RE4 Store",
    "store_address": "",
    "store_phone": "",
    "store_footer": "Thank you for your business!",
    "exchange_rate": "89000",  # LBP per 1 USD
    "tax_rate": "0",  # percent, applied after discount
    "lbp_rounding": "1000",  # LBP totals are rounded to this nearest step
    "low_stock_default": "5",
    "receipt_width_mm": "80",  # thermal roll width
    "printer_name": "",  # blank means the system default printer
    "backup_on_start": "1",
    "backup_keep": "20",
    "require_shift": "1",  # a till shift must be open before selling
    "allow_price_override": "1",  # employees may change a price with admin approval
    "login_max_attempts": "5",  # consecutive failures before the account locks; 0 = off
    "login_lockout_minutes": "5",  # how long a locked account stays locked
    "idle_lock_minutes": "15",  # lock the screen after this much inactivity; 0 = off
}


def ensure_directories() -> None:
    for directory in (DATA_DIR, RECEIPTS_DIR, BACKUPS_DIR, IMAGES_DIR, LOGS_DIR):
        directory.mkdir(parents=True, exist_ok=True)
