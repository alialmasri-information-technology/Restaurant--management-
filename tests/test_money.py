"""Money arithmetic — the part that must never be off by a cent."""

from __future__ import annotations

import unittest
from decimal import Decimal

from app.money import (
    compute_totals,
    fmt_lbp,
    fmt_usd,
    parse_amount,
    parse_int,
    to_lbp,
    usd,
)


class RoundingTests(unittest.TestCase):
    def test_rounds_half_up_like_a_till(self):
        self.assertEqual(usd("0.125"), Decimal("0.13"))
        self.assertEqual(usd("0.135"), Decimal("0.14"))

    def test_float_input_does_not_drift(self):
        # 0.1 + 0.2 in binary floats is 0.30000000000000004
        self.assertEqual(usd(0.1) + usd(0.2), Decimal("0.30"))

    def test_formatting(self):
        self.assertEqual(fmt_usd(1234.5), "$1,234.50")
        self.assertEqual(fmt_lbp(89000), "89,000 LBP")


class TotalsTests(unittest.TestCase):
    def test_subtotal_is_the_sum_of_lines(self):
        subtotal, _d, _t, total = compute_totals([(3, "0.50"), (2, "4.50")])
        self.assertEqual(subtotal, Decimal("10.50"))
        self.assertEqual(total, Decimal("10.50"))

    def test_discount_then_tax(self):
        subtotal, discount, tax, total = compute_totals(
            [(3, "0.50"), (2, "4.50")], discount="1.00", tax_rate="11"
        )
        self.assertEqual(subtotal, Decimal("10.50"))
        self.assertEqual(discount, Decimal("1.00"))
        self.assertEqual(tax, Decimal("1.05"))  # 11% of 9.50, rounded
        self.assertEqual(total, Decimal("10.55"))

    def test_discount_is_capped_at_the_subtotal(self):
        _s, discount, _t, total = compute_totals([(1, "5.00")], discount="999")
        self.assertEqual(discount, Decimal("5.00"))
        self.assertEqual(total, Decimal("0.00"))

    def test_empty_cart_totals_zero(self):
        self.assertEqual(compute_totals([]), (Decimal("0.00"),) * 4)

    def test_a_uniform_line_rate_matches_the_store_rate_exactly(self):
        # One percentage is one percentage, however it reaches the line.
        self.assertEqual(
            compute_totals([(3, "0.50", 0, "11"), (2, "4.50", 0, "11")],
                           discount="1.00", tax_rate="11"),
            compute_totals([(3, "0.50"), (2, "4.50")], discount="1.00", tax_rate="11"),
        )

    def test_category_rates_override_the_store_rate(self):
        # $4.50 line taxed at 20%, $0.50 line untaxed (0%), invoice discount 1.00.
        # The discount comes off before tax, shared in proportion to what each
        # line is worth: the 4.50 line's taxable share is 3.60, the 0.50's 0.40.
        subtotal, discount, tax, total = compute_totals(
            [(1, "4.50", 0, "20"), (1, "0.50", 0, "0")], discount="1.00", tax_rate="11"
        )
        self.assertEqual(subtotal, Decimal("5.00"))
        self.assertEqual(discount, Decimal("1.00"))
        self.assertEqual(tax, Decimal("0.72"))  # 20% of 3.60; the 0.50 line is untaxed
        self.assertEqual(total, Decimal("4.72"))

    def test_a_line_without_a_rate_uses_the_store_rate(self):
        subtotal, _d, tax, total = compute_totals(
            [(1, "4.50", 0, "20"), (1, "0.50")], tax_rate="10"
        )
        self.assertEqual(subtotal, Decimal("5.00"))
        # 20% of 4.50 plus 10% of 0.50.
        self.assertEqual(tax, Decimal("0.95"))
        self.assertEqual(total, Decimal("5.95"))

    def test_zero_taxed_lines_contribute_nothing(self):
        _s, _d, tax, _t = compute_totals(
            [(2, "1.00", 0, "0"), (1, "3.00", 0, "0")], tax_rate="15"
        )
        self.assertEqual(tax, Decimal("0.00"))


class LbpTests(unittest.TestCase):
    def test_converts_and_rounds_to_the_step(self):
        self.assertEqual(to_lbp("10.55", 89000, 1000), Decimal("939000"))

    def test_rounding_step_of_one_keeps_the_exact_amount(self):
        self.assertEqual(to_lbp("1.00", 89000, 1), Decimal("89000"))

    def test_large_rate_does_not_lose_precision(self):
        self.assertEqual(to_lbp("1.00", 95000, 5000), Decimal("95000"))

    def test_rounding_step_rounds_half_up(self):
        # 0.50 x 95,000 = 47,500, exactly half of a 5,000 step
        self.assertEqual(to_lbp("0.50", 95000, 5000), Decimal("50000"))


class ParsingTests(unittest.TestCase):
    def test_blank_is_zero(self):
        self.assertEqual(parse_amount(""), Decimal("0"))

    def test_thousands_separators_are_accepted(self):
        self.assertEqual(parse_amount("1,250.75"), Decimal("1250.75"))

    def test_negative_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_amount("-5", "discount")

    def test_text_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_amount("abc", "discount")

    def test_parse_int_enforces_minimum(self):
        self.assertEqual(parse_int("7", "qty", minimum=0), 7)
        with self.assertRaises(ValueError):
            parse_int("-1", "qty", minimum=0)
        with self.assertRaises(ValueError):
            parse_int("2.5", "qty")



class NegativeFormattingTests(unittest.TestCase):
    """A till variance is the one place these are seen, and $-5.00 looks broken."""

    def test_a_negative_amount_puts_the_sign_before_the_symbol(self):
        self.assertEqual(fmt_usd("-5"), "-$5.00")
        self.assertEqual(fmt_lbp("-5000"), "-5,000 LBP")

    def test_positive_amounts_are_unchanged(self):
        self.assertEqual(fmt_usd("5"), "$5.00")
        self.assertEqual(fmt_lbp("5000"), "5,000 LBP")

    def test_zero_carries_no_sign(self):
        self.assertEqual(fmt_usd(0), "$0.00")
        self.assertEqual(fmt_lbp(0), "0 LBP")


if __name__ == "__main__":
    unittest.main()
