"""Settings: what the shop is asked, and what happens when the answer is wrong.

Every figure here is read on a hot path — the exchange rate on every keystroke
in the amount-received box, the tax rate on every line added to a cart — and
each one is a row of free text that something else wrote. The rules these tests
protect are the same two throughout: never let a bad row stop the shop trading,
and never let one change what the shop charges without saying so.
"""

from __future__ import annotations

from app import config
from app.money import D
from app.services import settings as settings_service
from tests.support import DatabaseTestCase


class ExchangeRateTests(DatabaseTestCase):
    opens_shift = False

    def test_a_stored_rate_is_used_as_written(self):
        settings_service.set_value("exchange_rate", "90000")
        self.assertEqual(settings_service.exchange_rate(), D("90000"))

    def test_a_rate_that_is_not_a_number_falls_back(self):
        settings_service.set_value("exchange_rate", "ninety thousand")
        self.assertEqual(
            settings_service.exchange_rate(),
            D(config.DEFAULT_SETTINGS["exchange_rate"]),
        )

    def test_a_zero_rate_is_refused_rather_than_shown_as_zero_lbp(self):
        """Zero would price every LBP total on every screen at nothing.

        Selling already refuses a non-positive rate, so nothing could be
        recorded at one; the danger is the hours before anyone tries, with the
        till, the receipts and the labels all quietly reading zero.
        """
        settings_service.set_value("exchange_rate", "0")
        self.assertEqual(
            settings_service.exchange_rate(),
            D(config.DEFAULT_SETTINGS["exchange_rate"]),
        )

    def test_a_negative_rate_is_refused_too(self):
        settings_service.set_value("exchange_rate", "-89000")
        self.assertGreater(settings_service.exchange_rate(), 0)


class TaxRateTests(DatabaseTestCase):
    opens_shift = False

    def test_a_stored_rate_is_used_as_written(self):
        settings_service.set_value("tax_rate", "11")
        self.assertEqual(settings_service.tax_rate(), D("11"))

    def test_a_negative_rate_would_hand_money_back_and_is_refused(self):
        settings_service.set_value("tax_rate", "-5")
        self.assertEqual(
            settings_service.tax_rate(), D(config.DEFAULT_SETTINGS["tax_rate"])
        )

    def test_a_rate_above_a_hundred_percent_is_refused(self):
        settings_service.set_value("tax_rate", "150")
        self.assertEqual(
            settings_service.tax_rate(), D(config.DEFAULT_SETTINGS["tax_rate"])
        )

    def test_the_boundaries_are_allowed(self):
        """0% and 100% are both real answers, so neither may be thrown away."""
        settings_service.set_value("tax_rate", "0")
        self.assertEqual(settings_service.tax_rate(), D("0"))
        settings_service.set_value("tax_rate", "100")
        self.assertEqual(settings_service.tax_rate(), D("100"))


class RetentionTests(DatabaseTestCase):
    opens_shift = False

    def test_an_unreadable_retention_keeps_records_for_ever(self):
        """Erring towards keeping is the safe direction for a business record."""
        settings_service.set_value("audit_keep_days", "a fortnight")
        self.assertEqual(settings_service.audit_keep_days(), 0)

    def test_a_stated_retention_is_returned(self):
        settings_service.set_value("inventory_keep_days", "45")
        self.assertEqual(settings_service.inventory_keep_days(), 45)
