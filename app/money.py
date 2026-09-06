"""Money arithmetic.

USD amounts are computed with :class:`~decimal.Decimal` so that line totals,
discounts and tax never drift the way binary floats do, and are only converted
to ``float`` at the SQLite boundary. LBP is a display/settlement currency
derived from USD at the rate stored on each sale, and is rounded to a
configurable step because nobody hands out 1 LBP in change.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
ZERO = Decimal("0")


def D(value) -> Decimal:
    """Coerce anything money-ish to Decimal without going through binary float."""
    if isinstance(value, Decimal):
        return value
    if value is None or value == "":
        return ZERO
    return Decimal(str(value).strip().replace(",", ""))


def usd(value) -> Decimal:
    """Round to whole cents, half-up (what a till does, unlike banker's rounding)."""
    return D(value).quantize(CENT, rounding=ROUND_HALF_UP)


def to_float(value) -> float:
    """Convert to the float SQLite stores. Only call at the DB boundary."""
    return float(usd(value))


def to_lbp(usd_amount, rate, step=1000) -> Decimal:
    """Convert USD to LBP at ``rate``, rounded to the nearest ``step``."""
    rate = D(rate)
    step = D(step)
    raw = D(usd_amount) * rate
    if step <= ZERO:
        return raw.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return (raw / step).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * step


def fmt_usd(value) -> str:
    """Money for display. A negative amount reads -$5.00, never $-5.00."""
    amount = usd(value)
    sign = "-" if amount < ZERO else ""
    return f"{sign}${abs(amount):,.2f}"


def fmt_lbp(value) -> str:
    amount = D(value)
    sign = "-" if amount < ZERO else ""
    return f"{sign}{abs(amount):,.0f} LBP"


def parse_amount(text, field="amount") -> Decimal:
    """Parse user input into a non-negative Decimal, or raise ValueError."""
    if text is None or str(text).strip() == "":
        return ZERO
    try:
        value = D(text)
    except Exception as exc:  # any parse failure is surfaced as a message
        raise ValueError(f"{field.capitalize()} must be a number.") from exc
    if value < ZERO:
        raise ValueError(f"{field.capitalize()} cannot be negative.")
    return value


def parse_int(text, field="value", minimum=None) -> int:
    if text is None or str(text).strip() == "":
        raise ValueError(f"{field.capitalize()} is required.")
    try:
        value = int(str(text).strip())
    except ValueError as exc:
        raise ValueError(f"{field.capitalize()} must be a whole number.") from exc
    if minimum is not None and value < minimum:
        raise ValueError(f"{field.capitalize()} cannot be less than {minimum}.")
    return value


def line_total(qty, unit_price, line_discount=0) -> Decimal:
    """One cart line's value, never below zero however big the line discount."""
    gross = D(qty) * D(unit_price)
    return usd(max(ZERO, gross - D(line_discount)))


def compute_totals(lines, discount=0, tax_rate=0):
    """Total up a sale.

    ``lines`` is any iterable of ``(qty, unit_price)`` or
    ``(qty, unit_price, line_discount)`` tuples, or of objects exposing ``qty``,
    ``unit_price`` and optionally ``discount`` and ``tax_rate``. A line's own
    ``tax_rate`` — a percentage, or None — wins over the ``tax_rate`` argument,
    which is the store-wide rate a category rate may override. The ``discount``
    argument is the invoice-level discount in USD, clamped to the subtotal so a
    sale can never go negative, and shared across lines in proportion to what
    each is worth before its own tax.

    Returns ``(subtotal, discount, tax, total)`` as Decimals.
    """
    nets: list[tuple[Decimal, Decimal | None]] = []
    for line in lines:
        if isinstance(line, (tuple, list)):
            qty, unit_price = line[0], line[1]
            line_discount = line[2] if len(line) > 2 else ZERO
            line_rate = line[3] if len(line) > 3 else None
        else:
            qty, unit_price = line.qty, line.unit_price
            line_discount = getattr(line, "discount", ZERO)
            line_rate = getattr(line, "tax_rate", None)
        nets.append((line_total(qty, unit_price, line_discount), line_rate))
    subtotal = usd(sum((net for net, _ in nets), ZERO))

    discount = usd(max(ZERO, min(D(discount), subtotal)))
    resolved = {
        D(rate) if rate is not None else D(tax_rate) for _, rate in nets
    }
    if len(resolved) == 1:
        # The common case, and the exact arithmetic it has always been: one
        # rate on the discounted subtotal.
        tax = usd((subtotal - discount) * resolved.pop() / Decimal(100))
    else:
        # The invoice discount comes off before tax, so each line's taxable
        # share is what it keeps after its slice of that discount.
        factor = (subtotal - discount) / subtotal if subtotal > 0 else ZERO
        tax = usd(sum(
            (net * factor * D(rate if rate is not None else tax_rate) / Decimal(100)
             for net, rate in nets),
            ZERO,
        ))
    total = usd((subtotal - discount) + tax)
    return subtotal, discount, tax, total
