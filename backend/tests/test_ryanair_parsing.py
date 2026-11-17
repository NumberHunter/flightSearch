import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from flightsearch.domain import Money, PriceConfidence, Provider
from flightsearch.providers import ProviderResponseError
from flightsearch.providers.ryanair import (
    booking_url,
    parse_cheapest_per_day,
    parse_routes,
    parse_schedule,
)
from flightsearch.reference import InMemoryAirportRepository

FIXTURES = Path(__file__).parent / "fixtures" / "ryanair"
AIRPORTS = InMemoryAirportRepository.load_default()
SOF = AIRPORTS.get("SOF")
VAR = AIRPORTS.get("VAR")
BGY = AIRPORTS.get("BGY")
FETCHED_AT = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def parse(payload: object) -> list[Any]:
    return parse_cheapest_per_day(payload, origin=SOF, destination=BGY, fetched_at=FETCHED_AT)


def fare(**overrides: object) -> dict[str, object]:
    entry: dict[str, object] = {
        "day": "2026-12-01",
        "departureDate": "2026-12-01T11:30:00",
        "arrivalDate": "2026-12-01T12:35:00",
        "price": {"value": 17.99, "currencyCode": "EUR"},
        "soldOut": False,
        "unavailable": False,
    }
    entry.update(overrides)
    return entry


def payload(*fares: object) -> dict[str, object]:
    return {"outbound": {"fares": list(fares)}}


# --- Against the saved real responses --------------------------------------------------


def test_sof_bgy_december_has_a_leg_for_every_day_with_a_flight() -> None:
    legs = parse(load("cheapest_per_day_SOF_BGY_2026-12.json"))
    no_flight_days = {2, 9, 16, 23, 24, 25, 30}
    assert len(legs) == 31 - len(no_flight_days)
    assert {leg.departure.day for leg in legs}.isdisjoint(no_flight_days)


def test_sof_bgy_legs_are_exact_fares_in_euros() -> None:
    for leg in parse(load("cheapest_per_day_SOF_BGY_2026-12.json")):
        assert leg.provider is Provider.RYANAIR
        assert leg.airline == "FR"
        assert leg.price_confidence is PriceConfidence.EXACT
        assert leg.price is not None
        assert leg.price.currency == "EUR"
        assert leg.fetched_at == FETCHED_AT


def test_first_of_december_matches_the_fixture() -> None:
    first = parse(load("cheapest_per_day_SOF_BGY_2026-12.json"))[0]
    assert first.price == Money(Decimal("17.99"), "EUR")
    assert first.departure == datetime(2026, 12, 1, 11, 30, tzinfo=SOF.timezone)
    assert first.arrival == datetime(2026, 12, 1, 12, 35, tzinfo=BGY.timezone)


def test_overnight_flight_gets_each_airports_time_zone() -> None:
    legs = parse(load("cheapest_per_day_SOF_BGY_2026-12.json"))
    late = next(leg for leg in legs if leg.departure.date() == date(2026, 12, 14))
    assert late.departure == datetime(2026, 12, 14, 23, 20, tzinfo=SOF.timezone)
    assert late.arrival == datetime(2026, 12, 15, 0, 25, tzinfo=BGY.timezone)
    assert late.duration == timedelta(hours=2, minutes=5)  # not the naive 1h05
    assert late.price == Money(Decimal("15.99"), "EUR")


def test_a_route_ryanair_doesnt_fly_gives_no_legs() -> None:
    legs = parse_cheapest_per_day(
        load("cheapest_per_day_VAR_BGY_2026-12.json"),
        origin=VAR,
        destination=BGY,
        fetched_at=FETCHED_AT,
    )
    assert legs == []


def test_booking_url_points_at_the_departure_day() -> None:
    url = booking_url(SOF, BGY, date(2026, 12, 14))
    parts = urlsplit(url)
    assert parts.scheme == "https"
    assert parts.netloc == "www.ryanair.com"
    query = parse_qs(parts.query)
    assert query["dateOut"] == ["2026-12-14"]
    assert query["originIata"] == ["SOF"]
    assert query["destinationIata"] == ["BGY"]
    assert query["adults"] == ["1"]


# --- Expected gaps are skipped ---------------------------------------------------------


def test_skips_unavailable_and_sold_out_days() -> None:
    no_flight = fare(departureDate=None, arrivalDate=None, price=None, unavailable=True)
    assert parse(payload(no_flight, fare(soldOut=True))) == []


def test_skips_a_day_without_times() -> None:
    assert parse(payload(fare(departureDate=None))) == []


def test_known_times_without_a_price_become_a_check_leg() -> None:
    (leg,) = parse(payload(fare(price=None)))
    assert leg.price is None
    assert leg.price_confidence is PriceConfidence.CHECK


def test_a_null_price_value_becomes_a_check_leg() -> None:
    (leg,) = parse(payload(fare(price={"value": None, "currencyCode": "EUR"})))
    assert leg.price_confidence is PriceConfidence.CHECK


def test_price_strings_and_integers_are_accepted() -> None:
    legs = parse(
        payload(
            fare(price={"value": "17.99", "currencyCode": "EUR"}),
            fare(price={"value": 18, "currencyCode": "EUR"}),
        )
    )
    assert [leg.price for leg in legs] == [
        Money(Decimal("17.99"), "EUR"),
        Money(Decimal("18"), "EUR"),
    ]


def test_a_time_with_an_offset_is_converted_to_the_airports_zone() -> None:
    (leg,) = parse(payload(fare(departureDate="2026-12-01T09:30:00+00:00")))
    assert leg.departure == datetime(2026, 12, 1, 11, 30, tzinfo=SOF.timezone)
    assert leg.departure.tzinfo == SOF.timezone


# --- Unexpected shapes fail loudly -----------------------------------------------------


@pytest.mark.parametrize(
    "bad_payload",
    [[], None, {}, {"outbound": None}, {"outbound": {}}, {"outbound": {"fares": {}}}],
)
def test_rejects_an_unexpected_response_shape(bad_payload: object) -> None:
    with pytest.raises(ProviderResponseError):
        parse(bad_payload)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"departureDate": 1764588600}, "should be a string"),
        ({"departureDate": "1 Dec 2026, 11:30"}, "not a date-time"),
        ({"price": 17.99}, "'price' should be an object"),
        ({"price": {"value": "cheap", "currencyCode": "EUR"}}, "invalid price"),
        ({"price": {"value": True, "currencyCode": "EUR"}}, "should be a number"),
        ({"price": {"value": 17.99}}, "no currency code"),
        ({"price": {"value": -1, "currencyCode": "EUR"}}, "invalid price"),
        ({"arrivalDate": "2026-12-01T09:00:00"}, "not after"),
    ],
)
def test_rejects_a_malformed_fare(overrides: dict[str, object], message: str) -> None:
    with pytest.raises(ProviderResponseError, match=message):
        parse(payload(fare(**overrides)))


def test_rejects_a_fare_that_isnt_an_object() -> None:
    with pytest.raises(ProviderResponseError, match="object"):
        parse(payload("2026-12-01"))


def test_errors_name_the_provider_and_the_day() -> None:
    with pytest.raises(ProviderResponseError) as caught:
        parse(payload(fare(day="2026-12-07", departureDate="soon")))
    assert caught.value.provider is Provider.RYANAIR
    assert "2026-12-07" in str(caught.value)


# --- Timetable -------------------------------------------------------------------------


def schedule(payload: object, *, year: int = 2026, month: int = 12) -> list[Any]:
    return parse_schedule(
        payload, origin=SOF, destination=BGY, year=year, month=month, fetched_at=FETCHED_AT
    )


def flight(**overrides: object) -> dict[str, object]:
    entry: dict[str, object] = {
        "carrierCode": "FR",
        "number": "3401",
        "departureTime": "11:30",
        "arrivalTime": "12:35",
    }
    entry.update(overrides)
    return entry


def schedule_payload(*flights: object, day: object = 1) -> dict[str, object]:
    return {"month": 12, "days": [{"day": day, "flights": list(flights)}]}


def test_schedule_lists_every_flight_of_the_month() -> None:
    legs = schedule(load("schedule_SOF_BGY_2026-12.json"))
    days = {leg.departure.date() for leg in legs}
    assert len(days) == 24
    assert len(legs) == 34  # 10 of the 24 days have two flights


def test_schedule_and_fare_calendar_agree_on_which_days_have_flights() -> None:
    scheduled = {leg.departure.date() for leg in schedule(load("schedule_SOF_BGY_2026-12.json"))}
    priced = {leg.departure.date() for leg in parse(load("cheapest_per_day_SOF_BGY_2026-12.json"))}
    assert scheduled == priced


def test_schedule_legs_have_flight_numbers_but_no_prices() -> None:
    for leg in schedule(load("schedule_SOF_BGY_2026-12.json")):
        assert leg.airline == "FR"
        assert leg.flight_number is not None
        assert leg.flight_number.startswith("FR")
        assert leg.price is None
        assert leg.price_confidence is PriceConfidence.CHECK
        assert leg.fetched_at == FETCHED_AT


def test_schedule_finds_the_flights_the_fare_calendar_hides() -> None:
    legs = schedule(load("schedule_SOF_BGY_2026-12.json"))
    on_14th = [leg for leg in legs if leg.departure.date() == date(2026, 12, 14)]
    assert [(leg.flight_number, leg.departure.time().isoformat()) for leg in on_14th] == [
        ("FR3438", "13:35:00"),
        ("FR3401", "23:20:00"),
    ]


def test_schedule_moves_an_overnight_arrival_to_the_next_day() -> None:
    legs = schedule(load("schedule_SOF_BGY_2026-12.json"))
    late = next(leg for leg in legs if leg.flight_number == "FR3401" and leg.departure.day == 14)
    assert late.departure == datetime(2026, 12, 14, 23, 20, tzinfo=SOF.timezone)
    assert late.arrival == datetime(2026, 12, 15, 0, 25, tzinfo=BGY.timezone)
    assert late.duration == timedelta(hours=2, minutes=5)


def test_schedule_for_a_route_ryanair_doesnt_fly_is_empty() -> None:
    assert schedule(load("schedule_VAR_BGY_2026-12.json")) == []


def test_schedule_accepts_a_numeric_flight_number() -> None:
    (leg,) = schedule(schedule_payload(flight(number=3401)))
    assert leg.flight_number == "FR3401"


def test_schedule_uses_the_requested_year() -> None:
    payload = {"month": 1, "days": [{"day": 5, "flights": [flight()]}]}
    (leg,) = schedule(payload, year=2027, month=1)
    assert leg.departure.date() == date(2027, 1, 5)


@pytest.mark.parametrize(
    ("bad_payload", "message"),
    [
        ([], "JSON object"),
        ({"month": 11, "days": []}, "Asked for month 12"),
        ({"month": 12}, "'days' list"),
        ({"month": 12, "days": ["1"]}, "schedule day to be an object"),
        (schedule_payload(day=32), "doesn't exist"),
        (schedule_payload(day="1"), "day of the month"),
        ({"month": 12, "days": [{"day": 1}]}, "'flights' list"),
        (schedule_payload("FR3401"), "to be an object"),
        (schedule_payload(flight(carrierCode=None)), "carrier and number"),
        (schedule_payload(flight(number=None)), "carrier and number"),
        (schedule_payload(flight(departureTime=1130)), "should be a string"),
        (schedule_payload(flight(arrivalTime="half past noon")), "not HH:MM"),
        (schedule_payload(flight(carrierCode="RYANAIR")), "airline"),
    ],
)
def test_schedule_rejects_unexpected_shapes(bad_payload: object, message: str) -> None:
    with pytest.raises(ProviderResponseError, match=message):
        schedule(bad_payload)


# --- Routes ----------------------------------------------------------------------------


def test_routes_from_sofia() -> None:
    codes = parse_routes(load("routes_SOF.json"))
    assert len(codes) == 46
    assert codes == sorted(codes)
    assert {"BGY", "STN", "BCN"} <= set(codes)


def test_routes_from_varna() -> None:
    assert parse_routes(load("routes_VAR.json")) == ["FMM", "KRK", "KTW"]


def test_routes_skip_ryanair_connecting_services() -> None:
    payload = [
        {"arrivalAirport": {"code": "BGY"}, "connectingAirport": None},
        {"arrivalAirport": {"code": "AGP"}, "connectingAirport": {"code": "BGY"}},
    ]
    assert parse_routes(payload) == ["BGY"]


def test_routes_are_deduplicated() -> None:
    payload = [{"arrivalAirport": {"code": "BGY"}}, {"arrivalAirport": {"code": "bgy"}}]
    assert parse_routes(payload) == ["BGY"]


@pytest.mark.parametrize(
    ("bad_payload", "message"),
    [
        ({}, "JSON list"),
        (["BGY"], "route to be an object"),
        ([{"arrivalAirport": None}], "no arrival airport code"),
        ([{"arrivalAirport": {"code": 7}}], "no arrival airport code"),
        ([{"arrivalAirport": {"code": "BERGAMO"}}], "invalid airport code"),
    ],
)
def test_routes_reject_unexpected_shapes(bad_payload: object, message: str) -> None:
    with pytest.raises(ProviderResponseError, match=message):
        parse_routes(bad_payload)
