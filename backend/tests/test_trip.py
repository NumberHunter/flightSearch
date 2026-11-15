from datetime import UTC, date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from flightsearch.domain import (
    Airport,
    CurrencyMismatchError,
    ExchangeRates,
    Journey,
    Leg,
    Money,
    PriceConfidence,
    Provider,
    Trip,
)


def airport(iata: str, tz: str, lat: float, lon: float, country: str) -> Airport:
    return Airport(
        iata=iata,
        name=iata,
        city=iata,
        country_code=country,
        latitude=lat,
        longitude=lon,
        timezone=ZoneInfo(tz),
    )


SOF = airport("SOF", "Europe/Sofia", 42.6964, 23.4177, "BG")
VAR = airport("VAR", "Europe/Sofia", 43.2321, 27.8251, "BG")
BGY = airport("BGY", "Europe/Rome", 45.6694, 9.7089, "IT")


def journey(
    origin: Airport,
    destination: Airport,
    departs: tuple[int, int, int],  # (day, hour, minute) in December 2026, origin local time
    arrives: tuple[int, int, int],  # destination local time
    price: str | None = "15.99",
    currency: str = "EUR",
) -> Journey:
    leg = Leg(
        provider=Provider.RYANAIR,
        airline=None,
        flight_number=None,
        origin=origin,
        destination=destination,
        departure=datetime(2026, 12, *departs, tzinfo=origin.timezone),
        arrival=datetime(2026, 12, *arrives, tzinfo=destination.timezone),
        price=None if price is None else Money(Decimal(price), currency),
        price_confidence=PriceConfidence.CHECK if price is None else PriceConfidence.EXACT,
        booking_url=None,
        fetched_at=datetime(2026, 11, 9, tzinfo=UTC),
    )
    return Journey.of(leg)


# Real cheapest SOF->BGY on 14 Dec: departs 23:20 Sofia, lands 00:25 on the 15th in Bergamo.
OUT = journey(SOF, BGY, (14, 23, 20), (15, 0, 25), price="15.99")
# Made-up return on 20 Dec: 08:00 Bergamo -> 11:10 Sofia.
BACK = journey(BGY, SOF, (20, 8, 0), (20, 11, 10), price="24.99")

RATES = ExchangeRates(as_of=date(2026, 11, 14), per_euro={"RON": Decimal("4.97")})


# --- One-way ---


def test_one_way_trip() -> None:
    trip = Trip(OUT)
    assert not trip.is_round_trip
    assert trip.nights is None
    assert trip.journeys == (OUT,)
    assert trip.legs == OUT.legs
    assert trip.total_price() == Money(Decimal("15.99"), "EUR")


# --- Round trips ---


def test_round_trip_legs_and_price() -> None:
    trip = Trip(OUT, BACK)
    assert trip.is_round_trip
    assert trip.journeys == (OUT, BACK)
    assert trip.legs == OUT.legs + BACK.legs
    assert trip.total_price() == Money(Decimal("40.98"), "EUR")
    assert trip.price_confidence is PriceConfidence.EXACT


def test_nights_count_from_local_arrival_date_not_departure_date() -> None:
    # Left on the 14th but landed on the 15th; back on the 20th: 5 nights, not 6.
    assert Trip(OUT, BACK).nights == 5


def test_late_evening_return_is_still_counted_by_departure_date() -> None:
    late_back = journey(BGY, SOF, (20, 23, 30), (21, 2, 40))
    assert Trip(OUT, late_back).nights == 5


def test_same_day_return_is_zero_nights() -> None:
    same_day = journey(BGY, SOF, (15, 21, 0), (16, 0, 10))
    assert Trip(OUT, same_day).nights == 0


def test_return_to_a_different_home_airport_is_allowed() -> None:
    # Out from Sofia, back to Varna: allowed by default in the brief.
    to_varna = journey(BGY, VAR, (20, 9, 0), (20, 12, 15))
    assert Trip(OUT, to_varna).nights == 5


def test_unknown_return_price_makes_total_unknown() -> None:
    unpriced = journey(BGY, SOF, (20, 8, 0), (20, 11, 10), price=None)
    trip = Trip(OUT, unpriced)
    assert trip.total_price(RATES) is None
    assert trip.price_confidence is PriceConfidence.CHECK


def test_mixed_currencies_across_directions_convert_to_euros() -> None:
    # EUR out with one airline, RON back with another.
    back_in_ron = journey(BGY, SOF, (20, 8, 0), (20, 11, 10), price="120.00", currency="RON")
    trip = Trip(OUT, back_in_ron)
    assert trip.has_mixed_currencies
    # 120.00 RON / 4.97 = 24.14 EUR, plus 15.99 EUR.
    assert trip.total_price(RATES) == Money(Decimal("40.13"), "EUR")
    with pytest.raises(CurrencyMismatchError):
        trip.total_price()


def test_trip_is_immutable() -> None:
    trip = Trip(OUT, BACK)
    with pytest.raises(AttributeError):
        trip.inbound = None  # type: ignore[misc]


# --- Rejected trips ---


def test_return_departing_before_outbound_arrives_rejected() -> None:
    # 00:10 on the 15th in Bergamo is before the 00:25 landing.
    too_early = journey(BGY, SOF, (15, 0, 10), (15, 3, 20))
    with pytest.raises(ValueError, match="not after outbound arrival"):
        Trip(OUT, too_early)


def test_return_departing_at_arrival_instant_rejected() -> None:
    same_instant = journey(BGY, SOF, (15, 0, 25), (15, 3, 35))
    with pytest.raises(ValueError, match="not after outbound arrival"):
        Trip(OUT, same_instant)
