"""The first walk through the shop.

RE4 opens with sensible defaults — a store name, an exchange rate, a tax rate
— but sensible defaults are still *somebody else's* numbers. The wizard asks
for the shop's own ones once, the first time an administrator signs in, and
then never asks again: everything it touches stays editable on the Settings
screen for the rest of the shop's life.
"""

from __future__ import annotations

from app import config
from app.money import parse_amount
from app.services import audit
from app.services import settings as settings_service
from app.ui.widgets import FormModal, show_error


def needed(user) -> bool:
    """True when the person signing in should be shown the walkthrough."""
    if user is None or not user.is_admin:
        return False
    return settings_service.get("first_run_done", "0") != "1"


def maybe_first_run(parent, user) -> bool:
    """Open the walkthrough if it is owed. True when it was opened."""
    if not needed(user):
        return False
    FirstRunWizard(parent)
    return True


class FirstRunWizard(FormModal):
    """Five questions about the shop, asked once, answered in a minute."""

    def __init__(self, parent):
        values = settings_service.get_all()
        fields = [
            {"key": "store_name", "label": "What is the shop called?",
             "type": "entry", "value": values.get("store_name", config.APP_NAME)},
            {"key": "store_phone", "label": "Phone number (goes on receipts)",
             "type": "entry", "value": values.get("store_phone", "")},
            {"key": "store_address", "label": "Address (goes on receipts)",
             "type": "entry", "value": values.get("store_address", "")},
            {"key": "exchange_rate", "label": "Exchange rate — LBP per 1 USD",
             "type": "entry", "value": values.get("exchange_rate", ""),
             "hint": "The till shows prices in both currencies; this is the "
                     "rate it converts at."},
            {"key": "tax_rate", "label": "Tax rate, percent (0 if prices include it)",
             "type": "entry", "value": values.get("tax_rate", "")},
            {"key": "require_shift", "label": "A till shift must be open before selling",
             "type": "check",
             "value": values.get("require_shift", "1") not in ("", "0", "false", "False"),
             "hint": "Keeps every sale inside a counted drawer. Leave it on "
                     "unless you have a reason."},
        ]
        super().__init__(parent, "Welcome to RE4", fields, self._save,
                         submit_text="Save and open the shop", width=500)

    def _save(self, values) -> None:
        for key, label in (("exchange_rate", "exchange rate"), ("tax_rate", "tax rate")):
            try:
                parse_amount(values[key], label)
            except ValueError as exc:
                raise ValueError(str(exc)) from exc
        try:
            settings_service.set_many({
                "store_name": values["store_name"].strip() or config.APP_NAME,
                "store_phone": values["store_phone"].strip(),
                "store_address": values["store_address"].strip(),
                "exchange_rate": values["exchange_rate"].strip(),
                "tax_rate": values["tax_rate"].strip(),
                "require_shift": "1" if values["require_shift"] else "0",
                "first_run_done": "1",
            })
        except Exception as exc:  # noqa: BLE001 - the dialog stays open, said plainly
            show_error(self, exc, "Could not save")
            return
        audit.record("First-run setup completed", "setting", "",
                     f"{values['store_name']}, rate {values['exchange_rate']}")
