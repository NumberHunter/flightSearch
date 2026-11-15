"""Leg: a single flight between two airports, as seen by one provider."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from flightsearch.domain.airport import Airport
from flightsearch.domain.money import Money


class Provider(StrEnum):
    """Where the data came from (the adapter), not who operates the flight."""

    RYANAIR = "ryanair"
    WIZZAIR = "wizzair"
    EASYJET = "easyjet"
    SYNTHETIC = "synthetic"


class PriceConfidence(StrEnum):
    EXACT = "exact"  # the price shown is the fare for this specific flight
    CHECK = "check"  # the flight's time is known but its price isn't confirmed


def _is_airline_code(code: str) -> bool:
    # IATA airline designators are 2 characters, letters or digits (e.g. "FR", "W6", "U2").
    return len(code) == 2 and code.isascii() and code.isalnum() and code == code.upper()


@dataclass(frozen=True, slots=True, kw_only=True)
class Leg:
    """One flight. A Journey is made of one or more legs.

    `departure` and `arrival` are timezone-aware datetimes in the local time of the
    origin and destination airports respectively, so subtracting them gives the real
    flight duration even across time zones. `fetched_at` is in UTC.
    """

    provider: Provider
    airline: str | None  # IATA airline code; often unknown from fare calendars
    flight_number: str | None
    origin: Airport
    destination: Airport
    departure: datetime
    arrival: datetime
    price: Money | None  # None means unknown, never zero
    price_confidence: PriceConfidence
    booking_url: str | None
    fetched_at: datetime

    def __post_init__(self) -> None:
        if self.origin == self.destination:
            raise ValueError(f"Origin and destination are the same airport: {self.origin.iata}")

        for name, value, airport in (
            ("departure", self.departure, self.origin),
            ("arrival", self.arrival, self.destination),
        ):
            if value.tzinfo is None:
                raise ValueError(f"{name} must be timezone-aware: {value!r}")
            if value.tzinfo != airport.timezone:
                raise ValueError(
                    f"{name} must be in {airport.iata}'s time zone {airport.timezone.key}, "
                    f"got {value.tzinfo}"
                )

        if self.arrival <= self.departure:
            raise ValueError(
                f"Arrival {self.arrival.isoformat()} is not after "
                f"departure {self.departure.isoformat()}"
            )

        if self.fetched_at.utcoffset() != timedelta(0):
            raise ValueError(f"fetched_at must be a UTC datetime: {self.fetched_at!r}")

        if self.price_confidence is PriceConfidence.EXACT and self.price is None:
            raise ValueError("An EXACT leg must have a price")

        if self.airline is not None and not _is_airline_code(self.airline):
            raise ValueError(f"airline must be a 2-character IATA code: {self.airline!r}")

        if self.booking_url is not None and not self.booking_url.startswith("https://"):
            raise ValueError(f"booking_url must be an https URL: {self.booking_url!r}")

    @property
    def duration(self) -> timedelta:
        # Aware datetimes subtract as real instants, regardless of their time zones.
        return self.arrival - self.departure
