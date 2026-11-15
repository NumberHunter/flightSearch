"""Money: an exact amount in a specific currency, and conversion between currencies."""

from __future__ import annotations

import functools
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from types import MappingProxyType


class CurrencyMismatchError(ValueError):
    """Raised when combining or comparing amounts in different currencies."""


def _is_currency_code(code: str) -> bool:
    return len(code) == 3 and code.isascii() and code.isalpha() and code.isupper()


@functools.total_ordering
@dataclass(frozen=True, slots=True)
class Money:
    """An amount of money. Uses Decimal so sums and comparisons are exact.

    An unknown price is represented by `None` where a `Money` is expected,
    never by a zero amount.
    """

    amount: Decimal
    currency: str  # ISO 4217 code, e.g. "EUR"

    def __post_init__(self) -> None:
        # Type hints aren't enforced at runtime; a float here would silently lose precision.
        if not isinstance(self.amount, Decimal):
            raise TypeError(f"amount must be a Decimal, got {type(self.amount).__name__}")
        if not self.amount.is_finite():
            raise ValueError(f"amount must be finite, got {self.amount}")
        if self.amount < 0:
            raise ValueError(f"amount must not be negative, got {self.amount}")
        if not _is_currency_code(self.currency):
            raise ValueError(f"currency must be a 3-letter ISO 4217 code: {self.currency!r}")

    def __add__(self, other: Money) -> Money:
        self._require_same_currency(other)
        return Money(self.amount + other.amount, self.currency)

    def __lt__(self, other: Money) -> bool:
        self._require_same_currency(other)
        return self.amount < other.amount

    def _require_same_currency(self, other: Money) -> None:
        if self.currency != other.currency:
            raise CurrencyMismatchError(
                f"Cannot combine {self.currency} and {other.currency} amounts"
            )

    def __str__(self) -> str:
        return f"{self.amount} {self.currency}"


BASE_CURRENCY = "EUR"
CENT = Decimal("0.01")


class UnknownCurrencyError(KeyError):
    """Raised when converting a currency the exchange rates don't cover."""

    def __init__(self, currency: str) -> None:
        super().__init__(currency)
        self.currency = currency

    def __str__(self) -> str:
        return f"No exchange rate for {self.currency!r}"


@dataclass(frozen=True, slots=True)
class ExchangeRates:
    """A snapshot of exchange rates against the euro, as of one date.

    `per_euro` maps a currency to how many units of it one euro buys, the way the
    European Central Bank publishes them: {"RON": Decimal("4.97")} means 1 EUR = 4.97 RON.
    EUR itself is implicit (rate 1). This is pure data: fetching rates is I/O and
    happens outside the domain.
    """

    as_of: date
    per_euro: Mapping[str, Decimal]

    def __post_init__(self) -> None:
        for currency, rate in self.per_euro.items():
            if not _is_currency_code(currency):
                raise ValueError(f"Invalid currency code in rates: {currency!r}")
            if not isinstance(rate, Decimal) or not rate.is_finite() or rate <= 0:
                raise ValueError(f"Rate for {currency} must be a positive Decimal, got {rate!r}")
        if self.per_euro.get(BASE_CURRENCY, Decimal(1)) != 1:
            raise ValueError(f"The {BASE_CURRENCY} rate must be 1")
        # Copy into a read-only view so the frozen snapshot can't change behind our back.
        object.__setattr__(self, "per_euro", MappingProxyType(dict(self.per_euro)))

    def rate(self, currency: str) -> Decimal:
        if currency == BASE_CURRENCY:
            return Decimal(1)
        try:
            return self.per_euro[currency]
        except KeyError:
            raise UnknownCurrencyError(currency) from None

    def convert(self, money: Money, to: str = BASE_CURRENCY) -> Money:
        """Convert to another currency, rounded half-up to cents."""
        if money.currency == to:
            return money
        amount = money.amount / self.rate(money.currency) * self.rate(to)
        return Money(amount.quantize(CENT, rounding=ROUND_HALF_UP), to)


def has_mixed_currencies(prices: Iterable[Money | None]) -> bool:
    """Whether the known prices are in more than one currency."""
    return len({price.currency for price in prices if price is not None}) > 1


def sum_prices(prices: Sequence[Money | None], rates: ExchangeRates | None = None) -> Money | None:
    """Total a set of prices, e.g. the legs of a journey or trip.

    - Any unknown price (None) makes the total unknown: never treat it as zero.
    - All in one currency: the total is in that currency.
    - Mixed currencies: every price is converted to EUR with `rates`, and the total
      is in EUR. Without rates that raises CurrencyMismatchError. A converted total is
      an estimate; the amount charged depends on the card's rate at booking time.
    """
    if not prices:
        raise ValueError("Cannot total an empty set of prices")
    known = [price for price in prices if price is not None]
    if len(known) < len(prices):
        return None
    if has_mixed_currencies(known):
        if rates is None:
            raise CurrencyMismatchError(
                "Prices are in different currencies; exchange rates are needed to total them"
            )
        known = [rates.convert(price, BASE_CURRENCY) for price in known]
    # Money has no zero value to start from, so sum() can't be used directly.
    total = known[0]
    for price in known[1:]:
        total += price
    return total
