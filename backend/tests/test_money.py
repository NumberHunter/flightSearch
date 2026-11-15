from datetime import date
from decimal import Decimal

import pytest

from flightsearch.domain import (
    CurrencyMismatchError,
    ExchangeRates,
    Money,
    UnknownCurrencyError,
    sum_prices,
)


def eur(amount: str) -> Money:
    return Money(Decimal(amount), "EUR")


def test_add_same_currency() -> None:
    assert eur("17.99") + eur("15.99") == eur("33.98")


def test_add_different_currencies_raises() -> None:
    with pytest.raises(CurrencyMismatchError):
        eur("10") + Money(Decimal("50"), "RON")


def test_equality_ignores_trailing_zeros() -> None:
    assert eur("17.9") == eur("17.90")


def test_ordering_and_sorting() -> None:
    prices = [eur("30"), eur("15.99"), eur("172.79")]
    assert sorted(prices) == [eur("15.99"), eur("30"), eur("172.79")]
    assert min(prices) == eur("15.99")
    assert eur("1") <= eur("1")
    assert eur("2") > eur("1")


def test_comparing_different_currencies_raises() -> None:
    with pytest.raises(CurrencyMismatchError):
        _ = eur("500") < Money(Decimal("10"), "RON")


def test_zero_is_allowed() -> None:
    assert eur("0").amount == 0


def test_float_amount_rejected() -> None:
    with pytest.raises(TypeError, match="Decimal"):
        Money(17.99, "EUR")  # type: ignore[arg-type]


@pytest.mark.parametrize("amount", ["-0.01", "NaN", "Infinity"])
def test_invalid_amounts_rejected(amount: str) -> None:
    with pytest.raises(ValueError):
        eur(amount)


@pytest.mark.parametrize("currency", ["eur", "EURO", "EU", "", "E1R"])
def test_invalid_currencies_rejected(currency: str) -> None:
    with pytest.raises(ValueError, match="ISO 4217"):
        Money(Decimal("1"), currency)


def test_str() -> None:
    assert str(eur("17.99")) == "17.99 EUR"


# --- ExchangeRates ---

RATES = ExchangeRates(
    as_of=date(2026, 11, 14),
    per_euro={"RON": Decimal("4.97"), "GBP": Decimal("0.85"), "HUF": Decimal("390.5")},
)


def test_convert_to_euros() -> None:
    # 100 RON / 4.97 = 20.1207... -> 20.12 EUR
    assert RATES.convert(Money(Decimal("100"), "RON")) == eur("20.12")


def test_convert_from_euros() -> None:
    assert RATES.convert(eur("10"), to="RON") == Money(Decimal("49.70"), "RON")


def test_convert_between_two_non_euro_currencies() -> None:
    # 1000 HUF -> 2.5608... EUR -> 12.727... RON, rounded once at the end.
    assert RATES.convert(Money(Decimal("1000"), "HUF"), to="RON") == Money(Decimal("12.73"), "RON")


def test_convert_same_currency_is_unchanged() -> None:
    price = Money(Decimal("17.999"), "RON")
    assert RATES.convert(price, to="RON") is price


def test_convert_rounds_half_up_to_cents() -> None:
    rates = ExchangeRates(as_of=date(2026, 11, 14), per_euro={"XXX": Decimal("2")})
    # 0.05 / 2 = 0.025 -> 0.03 (half-up), not 0.02 (banker's rounding).
    assert rates.convert(Money(Decimal("0.05"), "XXX")) == eur("0.03")


def test_convert_unknown_currency_raises() -> None:
    with pytest.raises(UnknownCurrencyError, match="PLN"):
        RATES.convert(Money(Decimal("10"), "PLN"))


def test_rates_snapshot_is_read_only_and_copied() -> None:
    source = {"RON": Decimal("4.97")}
    rates = ExchangeRates(as_of=date(2026, 11, 14), per_euro=source)
    source["RON"] = Decimal("100")
    assert rates.rate("RON") == Decimal("4.97")
    with pytest.raises(TypeError):
        rates.per_euro["RON"] = Decimal("1")  # type: ignore[index]


def test_euro_rate_is_one() -> None:
    assert RATES.rate("EUR") == 1


@pytest.mark.parametrize(
    "per_euro",
    [
        {"RON": Decimal("0")},
        {"RON": Decimal("-4.97")},
        {"RON": 4.97},
        {"ron": Decimal("4.97")},
        {"EUR": Decimal("1.1")},
    ],
)
def test_invalid_rates_rejected(per_euro: dict[str, Decimal]) -> None:
    with pytest.raises(ValueError):
        ExchangeRates(as_of=date(2026, 11, 14), per_euro=per_euro)


# --- sum_prices ---


def test_sum_prices_of_nothing_is_an_error() -> None:
    with pytest.raises(ValueError, match="empty"):
        sum_prices([])


def test_sum_prices_single_price() -> None:
    assert sum_prices([eur("9.99")]) == eur("9.99")
