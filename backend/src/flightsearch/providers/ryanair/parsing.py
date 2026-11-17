"""Turning Ryanair's JSON responses into domain objects.

Covers three endpoints: the fare calendar (`cheapestPerDay`), the timetable (`timtbl`)
and the routes widget. Pure functions with no I/O, tested against the saved responses
in `tests/fixtures/ryanair/`. Their README lists what the real responses look like.

Parsing is defensive in two different ways:
- Expected gaps are skipped: days with no flight (`unavailable`) or sold out.
- Unexpected shapes raise `ProviderResponseError`, so a changed endpoint fails loudly
  instead of silently returning fewer flights.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from flightsearch.domain import Airport, Leg, Money, PriceConfidence, Provider, normalize_iata
from flightsearch.providers.base import ProviderResponseError

# Ryanair sells every flight under its own code, even those operated by its sister
# airlines (Malta Air, Buzz, Lauda Europe).
RYANAIR_AIRLINE_CODE = "FR"
BOOKING_URL = "https://www.ryanair.com/gb/en/trip/flights/select"


def booking_url(origin: Airport, destination: Airport, day: date) -> str:
    """Deep link to Ryanair's flight selection page for one adult, one way."""
    query = urlencode(
        {
            "adults": 1,
            "dateOut": day.isoformat(),
            "originIata": origin.iata,
            "destinationIata": destination.iata,
            "isConnectedFlight": "false",
            "isReturn": "false",
        }
    )
    return f"{BOOKING_URL}?{query}"


def parse_cheapest_per_day(
    payload: object, *, origin: Airport, destination: Airport, fetched_at: datetime
) -> list[Leg]:
    """Parse a `oneWayFares/{O}/{D}/cheapestPerDay` response: at most one leg per day.

    A day whose times are known but whose price is missing becomes a CHECK leg.
    """
    legs: list[Leg] = []
    for fare in _fares(payload):
        leg = _parse_fare(fare, origin=origin, destination=destination, fetched_at=fetched_at)
        if leg is not None:
            legs.append(leg)
    return legs


def _error(message: str) -> ProviderResponseError:
    return ProviderResponseError(Provider.RYANAIR, message)


def _fares(payload: object) -> list[Any]:
    if not isinstance(payload, dict):
        raise _error(f"Expected a JSON object, got {type(payload).__name__}")
    outbound = payload.get("outbound")
    if not isinstance(outbound, dict):
        raise _error("Response has no 'outbound' object")
    fares = outbound.get("fares")
    if not isinstance(fares, list):
        raise _error("Response has no 'outbound.fares' list")
    return fares


def _parse_fare(
    fare: object, *, origin: Airport, destination: Airport, fetched_at: datetime
) -> Leg | None:
    if not isinstance(fare, dict):
        raise _error(f"Expected each fare to be an object, got {type(fare).__name__}")
    day = fare.get("day", "?")

    if fare.get("unavailable") or fare.get("soldOut"):
        return None
    if fare.get("departureDate") is None or fare.get("arrivalDate") is None:
        return None  # nothing to show without times

    departure = _local_datetime(fare["departureDate"], origin.timezone, "departureDate", day)
    arrival = _local_datetime(fare["arrivalDate"], destination.timezone, "arrivalDate", day)
    price = _parse_price(fare.get("price"), day)

    try:
        return Leg(
            provider=Provider.RYANAIR,
            airline=RYANAIR_AIRLINE_CODE,
            flight_number=None,  # the fare calendar doesn't say
            origin=origin,
            destination=destination,
            departure=departure,
            arrival=arrival,
            price=price,
            price_confidence=PriceConfidence.CHECK if price is None else PriceConfidence.EXACT,
            booking_url=booking_url(origin, destination, departure.date()),
            fetched_at=fetched_at,
        )
    except ValueError as exc:
        raise _error(f"Fare for {day}: {exc}") from exc


def _local_datetime(value: object, timezone: ZoneInfo, field: str, day: object) -> datetime:
    """Ryanair sends airport-local times with no offset; attach the airport's zone."""
    if not isinstance(value, str):
        raise _error(f"Fare for {day}: '{field}' should be a string, got {value!r}")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise _error(f"Fare for {day}: '{field}' is not a date-time: {value!r}") from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone)
    return parsed.astimezone(timezone)  # not seen so far, but handle an offset if one appears


def _parse_price(value: object, day: object) -> Money | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise _error(f"Fare for {day}: 'price' should be an object, got {value!r}")
    amount = value.get("value")
    if amount is None:
        return None
    currency = value.get("currencyCode")
    if isinstance(amount, bool) or not isinstance(amount, int | float | str):
        raise _error(f"Fare for {day}: price value should be a number, got {amount!r}")
    if not isinstance(currency, str):
        raise _error(f"Fare for {day}: price has no currency code: {value!r}")
    try:
        # str() first: Decimal(17.99) would carry the float's binary rounding error.
        return Money(Decimal(str(amount)), currency)
    except (ArithmeticError, ValueError) as exc:
        raise _error(f"Fare for {day}: invalid price {value!r}: {exc}") from exc


# --- Timetable -------------------------------------------------------------------------


def parse_schedule(
    payload: object,
    *,
    origin: Airport,
    destination: Airport,
    year: int,
    month: int,
    fetched_at: datetime,
) -> list[Leg]:
    """Parse a `timtbl/3/schedules/{O}/{D}/years/{Y}/months/{M}` response.

    Every scheduled flight of the month, as CHECK legs without a price: the timetable
    has no prices and doesn't say whether a flight is sold out. The response only
    carries the month, so the year comes from the request.
    """
    if not isinstance(payload, dict):
        raise _error(f"Expected a JSON object, got {type(payload).__name__}")
    if payload.get("month") != month:
        raise _error(f"Asked for month {month}, got schedule for {payload.get('month')!r}")
    days = payload.get("days")
    if not isinstance(days, list):
        raise _error("Schedule has no 'days' list")

    legs: list[Leg] = []
    for entry in days:
        if not isinstance(entry, dict):
            raise _error(f"Expected each schedule day to be an object, got {entry!r}")
        day = _schedule_day(entry.get("day"), year, month)
        flights = entry.get("flights")
        if not isinstance(flights, list):
            raise _error(f"Schedule for {day.isoformat()} has no 'flights' list")
        for flight in flights:
            legs.append(
                _parse_scheduled_flight(
                    flight, day, origin=origin, destination=destination, fetched_at=fetched_at
                )
            )
    return legs


def _schedule_day(value: object, year: int, month: int) -> date:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _error(f"Schedule day should be a day of the month, got {value!r}")
    try:
        return date(year, month, value)
    except ValueError as exc:
        raise _error(f"Schedule day {value} doesn't exist in {year}-{month:02d}") from exc


def _parse_scheduled_flight(
    flight: object, day: date, *, origin: Airport, destination: Airport, fetched_at: datetime
) -> Leg:
    if not isinstance(flight, dict):
        raise _error(f"Expected each flight on {day.isoformat()} to be an object, got {flight!r}")
    carrier = flight.get("carrierCode")
    number = flight.get("number")
    if (
        not isinstance(carrier, str)
        or isinstance(number, bool)
        or not isinstance(number, str | int)
    ):
        raise _error(f"Flight on {day.isoformat()} has no valid carrier and number: {flight!r}")

    departure = datetime.combine(
        day, _clock_time(flight.get("departureTime"), day), tzinfo=origin.timezone
    )
    arrival = datetime.combine(
        day, _clock_time(flight.get("arrivalTime"), day), tzinfo=destination.timezone
    )
    # Times have no date. A flight past midnight shows as e.g. 23:20 -> 00:25, so an
    # arrival that isn't after the departure (compared as real instants) is the next day.
    if arrival <= departure:
        arrival += timedelta(days=1)

    try:
        return Leg(
            provider=Provider.RYANAIR,
            airline=carrier,
            flight_number=f"{carrier}{number}",
            origin=origin,
            destination=destination,
            departure=departure,
            arrival=arrival,
            price=None,
            price_confidence=PriceConfidence.CHECK,
            booking_url=booking_url(origin, destination, day),
            fetched_at=fetched_at,
        )
    except ValueError as exc:
        raise _error(f"Flight {carrier}{number} on {day.isoformat()}: {exc}") from exc


def _clock_time(value: object, day: date) -> time:
    if not isinstance(value, str):
        raise _error(f"Flight time on {day.isoformat()} should be a string, got {value!r}")
    try:
        return time.fromisoformat(value)
    except ValueError as exc:
        raise _error(f"Flight time on {day.isoformat()} is not HH:MM: {value!r}") from exc


# --- Routes ----------------------------------------------------------------------------


def parse_routes(payload: object) -> list[str]:
    """Parse a `searchWidget/routes/en/airport/{O}` response into sorted IATA codes.

    Only direct routes: an entry with a `connectingAirport` is Ryanair's own
    connecting service, not a flight from this airport, so it's skipped.
    """
    if not isinstance(payload, list):
        raise _error(f"Expected a JSON list of routes, got {type(payload).__name__}")
    codes: set[str] = set()
    for route in payload:
        if not isinstance(route, dict):
            raise _error(f"Expected each route to be an object, got {route!r}")
        if route.get("connectingAirport"):
            continue
        airport = route.get("arrivalAirport")
        code = airport.get("code") if isinstance(airport, dict) else None
        if not isinstance(code, str):
            raise _error(f"Route has no arrival airport code: {route!r}")
        try:
            codes.add(normalize_iata(code))
        except ValueError as exc:
            raise _error(f"Route has an invalid airport code: {code!r}") from exc
    return sorted(codes)
