"""Domain model: pure data types and rules, with no I/O."""

from flightsearch.domain.airport import Airport, great_circle_km, normalize_iata
from flightsearch.domain.journey import Journey
from flightsearch.domain.leg import Leg, PriceConfidence, Provider
from flightsearch.domain.money import (
    BASE_CURRENCY,
    CurrencyMismatchError,
    ExchangeRates,
    Money,
    UnknownCurrencyError,
    sum_prices,
)
from flightsearch.domain.trip import Trip

__all__ = [
    "BASE_CURRENCY",
    "Airport",
    "CurrencyMismatchError",
    "ExchangeRates",
    "Journey",
    "Leg",
    "Money",
    "PriceConfidence",
    "Provider",
    "Trip",
    "UnknownCurrencyError",
    "great_circle_km",
    "normalize_iata",
    "sum_prices",
]
