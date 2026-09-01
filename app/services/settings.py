"""Store settings, persisted as key/value rows."""

from __future__ import annotations

from decimal import Decimal

from app import config, db
from app.money import D


def get(key: str, default: str | None = None) -> str:
    value = db.scalar("SELECT value FROM settings WHERE key = ?", (key,))
    if value is None:
        return config.DEFAULT_SETTINGS.get(key, default if default is not None else "")
    return value


def get_all() -> dict[str, str]:
    values = dict(config.DEFAULT_SETTINGS)
    for row in db.query("SELECT key, value FROM settings"):
        values[row["key"]] = row["value"]
    return values


def set_value(key: str, value) -> None:
    db.execute(
        """
        INSERT INTO settings (key, value) VALUES (?, ?)
        ON CONFLICT (key) DO UPDATE SET value = excluded.value
        """,
        (key, str(value)),
    )


def set_many(values: dict) -> None:
    with db.transaction() as conn:
        for key, value in values.items():
            conn.execute(
                """
                INSERT INTO settings (key, value) VALUES (?, ?)
                ON CONFLICT (key) DO UPDATE SET value = excluded.value
                """,
                (key, str(value)),
            )


def _decimal(key: str, fallback: str) -> Decimal:
    try:
        return D(get(key, fallback))
    except Exception:  # noqa: BLE001 - a corrupt setting must not break the till
        return D(fallback)


def exchange_rate() -> Decimal:
    """LBP per 1 USD."""
    return _decimal("exchange_rate", config.DEFAULT_SETTINGS["exchange_rate"])


def tax_rate() -> Decimal:
    """Tax percentage applied to the discounted subtotal."""
    return _decimal("tax_rate", config.DEFAULT_SETTINGS["tax_rate"])


def lbp_rounding() -> Decimal:
    return _decimal("lbp_rounding", config.DEFAULT_SETTINGS["lbp_rounding"])


def low_stock_default() -> int:
    return int(_decimal("low_stock_default", config.DEFAULT_SETTINGS["low_stock_default"]))


def store_info() -> dict[str, str]:
    values = get_all()
    return {
        "name": values.get("store_name", config.APP_NAME),
        "address": values.get("store_address", ""),
        "phone": values.get("store_phone", ""),
        "footer": values.get("store_footer", ""),
    }


def _flag(key: str) -> bool:
    return get(key, config.DEFAULT_SETTINGS.get(key, "0")).strip() not in ("", "0", "false", "False")


def receipt_width_mm() -> int:
    """Thermal roll width. Anything unrecognised falls back to 80 mm."""
    value = get("receipt_width_mm", config.DEFAULT_SETTINGS["receipt_width_mm"]).strip()
    return 58 if value.startswith("58") else 80


def printer_name() -> str:
    """Blank means 'use whatever the operating system considers default'."""
    return get("printer_name", "").strip()


def backup_on_start() -> bool:
    return _flag("backup_on_start")


def backup_keep() -> int:
    try:
        return max(1, int(_decimal("backup_keep", config.DEFAULT_SETTINGS["backup_keep"])))
    except Exception:  # noqa: BLE001
        return 20


def require_shift() -> bool:
    return _flag("require_shift")


def allow_price_override() -> bool:
    return _flag("allow_price_override")
