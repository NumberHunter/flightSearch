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
from flightsearch.domain.search_request import (
    DEFAULT_MAX_LAYOVER,
    DEFAULT_MIN_CONNECTION,
    MAX_SUPPORTED_STOPS,
    AirportSpec,
    BeThereOn,
    DateMode,
    DateWindow,
    ExactDates,
    FlexibleDates,
    GroupSpec,
    LocationSpec,
    RadiusSpec,
    SearchRequest,
    StayRange,
)
from flightsearch.domain.trip import Trip

__all__ = [
    "BASE_CURRENCY",
    "DEFAULT_MAX_LAYOVER",
    "DEFAULT_MIN_CONNECTION",
    "MAX_SUPPORTED_STOPS",
    "Airport",
    "AirportSpec",
    "BeThereOn",
    "CurrencyMismatchError",
    "DateMode",
    "DateWindow",
    "ExactDates",
    "ExchangeRates",
    "FlexibleDates",
    "GroupSpec",
    "Journey",
    "Leg",
    "LocationSpec",
    "Money",
    "PriceConfidence",
    "Provider",
    "RadiusSpec",
    "SearchRequest",
    "StayRange",
    "Trip",
    "UnknownCurrencyError",
    "great_circle_km",
    "normalize_iata",
    "sum_prices",
]
