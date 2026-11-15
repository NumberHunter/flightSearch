from datetime import UTC, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from flightsearch.domain import Airport, Leg, Money, PriceConfidence, Provider

SOFIA_TZ = ZoneInfo("Europe/Sofia")
ROME_TZ = ZoneInfo("Europe/Rome")

SOF = Airport(
    iata="SOF",
    name="Sofia Airport",
    city="Sofia",
    country_code="BG",
    latitude=42.6964,
    longitude=23.4177,
    timezone=SOFIA_TZ,
)
BGY = Airport(
    iata="BGY",
    name="Il Caravaggio International Airport",
    city="Orio al Serio (BG)",
    country_code="IT",
    latitude=45.6694,
    longitude=9.7089,
    timezone=ROME_TZ,
)


def make_leg(**overrides: object) -> Leg:
    """The real cheapest SOF->BGY fare on 2026-12-14 from the Ryanair fixture.

    Departs 23:20 Sofia (UTC+2), arrives 00:25 next day in Bergamo (UTC+1).
    """
    fields: dict[str, object] = {
        "provider": Provider.RYANAIR,
        "airline": None,
        "flight_number": None,
        "origin": SOF,
        "destination": BGY,
        "departure": datetime(2026, 12, 14, 23, 20, tzinfo=SOFIA_TZ),
        "arrival": datetime(2026, 12, 15, 0, 25, tzinfo=ROME_TZ),
        "price": Money(Decimal("15.99"), "EUR"),
        "price_confidence": PriceConfidence.EXACT,
        "booking_url": "https://www.ryanair.com/gb/en/trip/flights/select?adults=1",
        "fetched_at": datetime(2026, 11, 9, 0, 15, tzinfo=UTC),
    }
    fields.update(overrides)
    return Leg(**fields)  # type: ignore[arg-type]


def test_duration_across_time_zones_and_midnight() -> None:
    leg = make_leg()
    # Naively 23:20 -> 00:25 looks like 1h05; Sofia is an hour ahead of Bergamo.
    assert leg.duration == timedelta(hours=2, minutes=5)
    assert leg.arrival.date() > leg.departure.date()


def test_check_leg_may_have_unknown_price() -> None:
    leg = make_leg(price=None, price_confidence=PriceConfidence.CHECK)
    assert leg.price is None


def test_optional_fields_may_be_none() -> None:
    leg = make_leg(airline=None, flight_number=None, booking_url=None)
    assert leg.airline is None


def test_known_airline_code_accepted() -> None:
    assert make_leg(airline="FR").airline == "FR"
    assert make_leg(airline="W6").airline == "W6"


def test_leg_is_immutable() -> None:
    leg = make_leg()
    with pytest.raises(AttributeError):
        leg.price = None  # type: ignore[misc]


def test_positional_arguments_rejected() -> None:
    with pytest.raises(TypeError):
        Leg(Provider.RYANAIR)  # type: ignore[call-arg]


def test_exact_leg_requires_price() -> None:
    with pytest.raises(ValueError, match="EXACT"):
        make_leg(price=None, price_confidence=PriceConfidence.EXACT)


def test_same_origin_and_destination_rejected() -> None:
    with pytest.raises(ValueError, match="same airport"):
        make_leg(
            destination=SOF,
            arrival=datetime(2026, 12, 15, 1, 25, tzinfo=SOFIA_TZ),
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"departure": datetime(2026, 12, 14, 23, 20)},  # noqa: DTZ001 - naive on purpose
        {"arrival": datetime(2026, 12, 15, 0, 25)},  # noqa: DTZ001 - naive on purpose
    ],
)
def test_naive_datetimes_rejected(overrides: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        make_leg(**overrides)


def test_time_in_wrong_airport_time_zone_rejected() -> None:
    # Same instant, but expressed in Rome time instead of Sofia time.
    departure_in_rome = datetime(2026, 12, 14, 22, 20, tzinfo=ROME_TZ)
    with pytest.raises(ValueError, match="SOF's time zone"):
        make_leg(departure=departure_in_rome)


def test_arrival_before_departure_rejected() -> None:
    # 00:25 Rome on the 14th is before 23:20 Sofia on the 14th.
    with pytest.raises(ValueError, match="not after"):
        make_leg(arrival=datetime(2026, 12, 14, 0, 25, tzinfo=ROME_TZ))


def test_arrival_equal_to_departure_rejected() -> None:
    # 22:20 Rome is the same instant as 23:20 Sofia.
    with pytest.raises(ValueError, match="not after"):
        make_leg(arrival=datetime(2026, 12, 14, 22, 20, tzinfo=ROME_TZ))


@pytest.mark.parametrize(
    "fetched_at",
    [
        datetime(2026, 11, 9, 2, 15, tzinfo=SOFIA_TZ),
        datetime(2026, 11, 9, 0, 15),  # noqa: DTZ001 - naive on purpose
    ],
)
def test_fetched_at_must_be_utc(fetched_at: datetime) -> None:
    with pytest.raises(ValueError, match="UTC"):
        make_leg(fetched_at=fetched_at)


@pytest.mark.parametrize("airline", ["fr", "FRX", "F", "", "F-"])
def test_invalid_airline_code_rejected(airline: str) -> None:
    with pytest.raises(ValueError, match="airline"):
        make_leg(airline=airline)


@pytest.mark.parametrize("url", ["", "http://www.ryanair.com", "ryanair.com"])
def test_invalid_booking_url_rejected(url: str) -> None:
    with pytest.raises(ValueError, match="booking_url"):
        make_leg(booking_url=url)
