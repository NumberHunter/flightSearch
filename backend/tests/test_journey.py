from datetime import UTC, date, datetime, timedelta
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
    UnknownCurrencyError,
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


SOF = airport("SOF", "Europe/Sofia", 42.6964, 23.4177, "BG")  # UTC+2 in December
BGY = airport("BGY", "Europe/Rome", 45.6694, 9.7089, "IT")  # UTC+1
MXP = airport("MXP", "Europe/Rome", 45.6306, 8.7281, "IT")  # UTC+1
STN = airport("STN", "Europe/London", 51.885, 0.235, "GB")  # UTC+0


def leg(
    origin: Airport,
    destination: Airport,
    departs: tuple[int, int, int],  # (day, hour, minute) in December 2026, origin local time
    arrives: tuple[int, int, int],  # (day, hour, minute), destination local time
    price: str | None = "15.99",
    currency: str = "EUR",
) -> Leg:
    return Leg(
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


# The real cheapest SOF->BGY fare on 14 Dec: 23:20 Sofia -> 00:25 Bergamo next day.
SOF_BGY = leg(SOF, BGY, (14, 23, 20), (15, 0, 25), price="15.99")
# A made-up onward flight: 06:30 Bergamo -> 07:30 London (a 2h flight; London is UTC+0).
BGY_STN = leg(BGY, STN, (15, 6, 30), (15, 7, 30), price="29.99")


# --- Direct journeys ---


def test_direct_journey_mirrors_its_leg() -> None:
    journey = Journey.of(SOF_BGY)
    assert journey.origin == SOF
    assert journey.destination == BGY
    assert journey.departure == SOF_BGY.departure
    assert journey.arrival == SOF_BGY.arrival
    assert journey.duration == timedelta(hours=2, minutes=5)
    assert journey.stops == 0
    assert not journey.is_self_transfer
    assert journey.layovers == ()
    assert journey.total_price() == Money(Decimal("15.99"), "EUR")
    assert journey.price_confidence is PriceConfidence.EXACT


# --- Self-transfers ---


def test_one_stop_journey() -> None:
    journey = Journey.of(SOF_BGY, BGY_STN)
    assert journey.origin == SOF
    assert journey.destination == STN
    assert journey.stops == 1
    assert journey.is_self_transfer
    # On the ground in Bergamo from 00:25 to 06:30.
    assert journey.layovers == (timedelta(hours=6, minutes=5),)
    # 21:20 UTC on the 14th to 07:30 UTC on the 15th.
    assert journey.duration == timedelta(hours=10, minutes=10)
    assert journey.total_price() == Money(Decimal("45.98"), "EUR")


def test_duration_is_flights_plus_layovers() -> None:
    journey = Journey.of(SOF_BGY, BGY_STN)
    flying = SOF_BGY.duration + BGY_STN.duration
    assert journey.duration == flying + sum(journey.layovers, timedelta())


def test_unknown_leg_price_makes_total_unknown() -> None:
    unpriced = leg(BGY, STN, (15, 6, 30), (15, 7, 30), price=None)
    journey = Journey.of(SOF_BGY, unpriced)
    assert journey.total_price() is None
    assert journey.price_confidence is PriceConfidence.CHECK


def test_legs_from_a_list_are_stored_as_a_tuple() -> None:
    legs = [SOF_BGY, BGY_STN]
    journey = Journey(legs)  # type: ignore[arg-type]
    legs.clear()
    assert journey.legs == (SOF_BGY, BGY_STN)


def test_journey_is_immutable() -> None:
    journey = Journey.of(SOF_BGY)
    with pytest.raises(AttributeError):
        journey.legs = ()  # type: ignore[misc]


# --- Rejected journeys ---


def test_empty_journey_rejected() -> None:
    with pytest.raises(ValueError, match="at least one leg"):
        Journey(())


def test_legs_must_connect_at_the_same_airport() -> None:
    # Landing at BGY and leaving from MXP (both "Milan") is an airport change,
    # which the brief disallows by default.
    mxp_stn = leg(MXP, STN, (15, 9, 0), (15, 10, 0))
    with pytest.raises(ValueError, match="don't connect"):
        Journey.of(SOF_BGY, mxp_stn)


def test_connection_departing_before_arrival_rejected() -> None:
    # Departs 00:10 Bergamo, before the 00:25 arrival.
    too_early = leg(BGY, STN, (15, 0, 10), (15, 1, 10))
    with pytest.raises(ValueError, match="not after arrival"):
        Journey.of(SOF_BGY, too_early)


def test_connection_departing_at_arrival_instant_rejected() -> None:
    same_minute = leg(BGY, STN, (15, 0, 25), (15, 1, 25))
    with pytest.raises(ValueError, match="not after arrival"):
        Journey.of(SOF_BGY, same_minute)


def test_short_connection_is_allowed_by_the_domain() -> None:
    # 20 minutes is unrealistic for a self-transfer, but minimum connection time is
    # search policy enforced by the itinerary builder, not a domain invariant.
    tight = leg(BGY, STN, (15, 0, 45), (15, 1, 45))
    assert Journey.of(SOF_BGY, tight).layovers == (timedelta(minutes=20),)


RATES = ExchangeRates(
    as_of=date(2026, 11, 14),
    per_euro={"GBP": Decimal("0.85"), "RON": Decimal("4.97")},
)

# A EUR fare out of Sofia, then a GBP fare out of Bergamo on another airline.
BGY_STN_GBP = leg(BGY, STN, (15, 6, 30), (15, 7, 30), price="25.00", currency="GBP")


def test_mixed_currency_journey_keeps_original_leg_prices() -> None:
    journey = Journey.of(SOF_BGY, BGY_STN_GBP)
    assert journey.has_mixed_currencies
    assert [leg.price for leg in journey.legs] == [
        Money(Decimal("15.99"), "EUR"),
        Money(Decimal("25.00"), "GBP"),
    ]


def test_mixed_currency_total_is_converted_to_euros() -> None:
    journey = Journey.of(SOF_BGY, BGY_STN_GBP)
    # 25.00 GBP / 0.85 = 29.41 EUR, plus 15.99 EUR.
    assert journey.total_price(RATES) == Money(Decimal("45.40"), "EUR")


def test_mixed_currency_total_without_rates_raises() -> None:
    with pytest.raises(CurrencyMismatchError, match="exchange rates"):
        Journey.of(SOF_BGY, BGY_STN_GBP).total_price()


def test_mixed_currency_total_with_missing_rate_raises() -> None:
    no_gbp = ExchangeRates(as_of=date(2026, 11, 14), per_euro={"RON": Decimal("4.97")})
    with pytest.raises(UnknownCurrencyError, match="GBP"):
        Journey.of(SOF_BGY, BGY_STN_GBP).total_price(no_gbp)


def test_unknown_price_wins_over_conversion() -> None:
    unpriced = leg(BGY, STN, (15, 6, 30), (15, 7, 30), price=None)
    assert Journey.of(SOF_BGY, unpriced).total_price(RATES) is None


def test_single_foreign_currency_journey_is_not_converted() -> None:
    # All legs in GBP: no mismatch, so the total stays in GBP even when rates are given.
    stn_bgy = leg(STN, BGY, (16, 9, 0), (16, 12, 0), price="20.00", currency="GBP")
    journey = Journey.of(BGY_STN_GBP, stn_bgy)
    assert not journey.has_mixed_currencies
    assert journey.total_price(RATES) == Money(Decimal("45.00"), "GBP")


def test_single_currency_journey_is_not_mixed() -> None:
    assert not Journey.of(SOF_BGY, BGY_STN).has_mixed_currencies
