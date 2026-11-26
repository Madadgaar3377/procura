"""Decimal-only commercial arithmetic shared by quotations, purchase orders and invoices.

All amounts are rounded half-up to 2 decimals at the line level and at totals.
Floats are never used.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal('0.01')
ZERO = Decimal('0')
HUNDRED = Decimal('100')


def d(value, default: str = '0') -> Decimal:
    if value is None or value == '':
        return Decimal(default)
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def money(value) -> Decimal:
    return d(value).quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Line:
    quantity: Decimal
    unit_price: Decimal
    discount_percent: Decimal = ZERO
    vat_rate: Decimal = Decimal('0.15')

    @property
    def gross(self) -> Decimal:
        return money(self.quantity * self.unit_price)

    @property
    def discount(self) -> Decimal:
        return money(self.quantity * self.unit_price * self.discount_percent / HUNDRED)

    @property
    def net(self) -> Decimal:
        return self.gross - self.discount


def totals(lines: list[Line], *, header_discount=ZERO, delivery_charge=ZERO, delivery_vat_rate=ZERO) -> dict[str, Decimal]:
    """Subtotal (gross), discount (line + header), VAT and total.

    VAT is charged on each line's net amount; a header discount is apportioned
    across lines pro rata before VAT, so VAT is never charged on discounted value.
    """
    header_discount = d(header_discount)
    delivery_charge = d(delivery_charge)
    delivery_vat_rate = d(delivery_vat_rate)
    for line in lines:
        if line.quantity < 0 or line.unit_price < 0 or not ZERO <= line.discount_percent <= HUNDRED or not ZERO <= line.vat_rate <= 1:
            raise ValueError('Invalid line quantity, price, discount or VAT rate')
    subtotal = sum((line.gross for line in lines), ZERO)
    line_discount = sum((line.discount for line in lines), ZERO)
    net = subtotal - line_discount
    if header_discount < 0 or header_discount > net or delivery_charge < 0 or not ZERO <= delivery_vat_rate <= 1:
        raise ValueError('Invalid discount or delivery charge')
    vat_base = sum((line.net * line.vat_rate for line in lines), ZERO)
    if net:
        vat_base = vat_base * (net - header_discount) / net
    vat = money(vat_base + delivery_charge * delivery_vat_rate)
    discount = money(line_discount + header_discount)
    return {
        'subtotal': money(subtotal),
        'discount': discount,
        'delivery_charge': money(delivery_charge),
        'vat': vat,
        'total': money(subtotal - discount + delivery_charge + vat),
    }


def amount_in_words(amount: Decimal, currency: str = 'SAR') -> str:
    """English amount in words for the tax invoice payment block."""
    ones = ['', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten', 'eleven',
            'twelve', 'thirteen', 'fourteen', 'fifteen', 'sixteen', 'seventeen', 'eighteen', 'nineteen']
    tens = ['', '', 'twenty', 'thirty', 'forty', 'fifty', 'sixty', 'seventy', 'eighty', 'ninety']

    def chunk(n: int) -> str:
        words = []
        if n >= 100:
            words.append(ones[n // 100] + ' hundred')
            n %= 100
        if n >= 20:
            words.append(tens[n // 10] + (('-' + ones[n % 10]) if n % 10 else ''))
        elif n:
            words.append(ones[n])
        return ' '.join(words)

    amount = money(amount)
    whole = int(amount)
    fraction = int((amount - whole) * 100)
    if whole == 0:
        text = 'zero'
    else:
        parts = []
        for value, label in ((10**9, 'billion'), (10**6, 'million'), (1000, 'thousand'), (1, '')):
            if whole >= value:
                parts.append((chunk(whole // value) + (' ' + label if label else '')).strip())
                whole %= value
        text = ' '.join(parts)
    major = {'SAR': 'Saudi riyals', 'USD': 'US dollars', 'EUR': 'euros', 'AED': 'UAE dirhams', 'GBP': 'pounds sterling'}.get(currency, currency)
    minor = {'SAR': 'halalas', 'AED': 'fils', 'GBP': 'pence'}.get(currency, 'cents')
    result = f'{text} {major}'
    if fraction:
        result += f' and {chunk(fraction)} {minor}'
    return result[0].upper() + result[1:] + ' only'
