"""SearchRequest: what the user asked for, before any airports are resolved or flights fetched.

The request stores the user's intent ("any Milan airport", "within 150 km of Varna"),
not the airports it currently expands to. A resolver in `flightsearch.reference` turns
the location specs into airports, and the itinerary builder turns the date mode into
constraints on journeys and trips.

Checks that depend on today's date (a window in the past, beyond the airlines' ~180-day
calendar) belong to the API and CLI layer, not here, so these types stay pure and tests
don't start failing as time passes.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, timedelta

from flightsearch.domain.airport import normalize_iata
from flightsearch.domain.money import BASE_CURRENCY

# --- Locations -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AirportSpec:
    """A single airport, e.g. VAR."""

    code: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", normalize_iata(self.code))


@dataclass(frozen=True, slots=True)
class GroupSpec:
    """A named airport group, e.g. MIL for BGY + MXP + LIN.

    Which airports belong to the group is reference data, looked up at resolve time,
    so a saved search picks up airports added to the group later.
    """

    group: str

    def __post_init__(self) -> None:
        group = self.group.strip().upper()
        if not group:
            raise ValueError("Airport group name can't be empty")
        object.__setattr__(self, "group", group)


@dataclass(frozen=True, slots=True)
class RadiusSpec:
    """Every airport within `km` kilometres of an airport, e.g. 150 km around VAR.

    The centre airport itself is included.
    """

    code: str
    km: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", normalize_iata(self.code))
        if not math.isfinite(self.km) or self.km <= 0:
            raise ValueError(f"Radius must be a positive number of km, got {self.km}")


LocationSpec = AirportSpec | GroupSpec | RadiusSpec


def _location_set(specs: tuple[LocationSpec, ...], name: str) -> tuple[LocationSpec, ...]:
    """Drop exact duplicates (keeping order) and reject an empty set."""
    unique = tuple(dict.fromkeys(specs))
    if not unique:
        raise ValueError(f"{name} needs at least one location")
    return unique


# --- Dates -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DateWindow:
    """An inclusive range of dates, e.g. 1-20 December."""

    start: date
    end: date

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError(
                f"Date window ends ({self.end.isoformat()}) before it starts "
                f"({self.start.isoformat()})"
            )

    @classmethod
    def single(cls, day: date) -> DateWindow:
        return cls(day, day)

    def __contains__(self, day: date) -> bool:
        return self.start <= day <= self.end

    @property
    def length(self) -> int:
        """Number of days in the window, counting both ends."""
        return (self.end - self.start).days + 1

    def days(self) -> Iterator[date]:
        for offset in range(self.length):
            yield self.start + timedelta(days=offset)


@dataclass(frozen=True, slots=True)
class StayRange:
    """An inclusive range of nights at the destination; see `Trip.nights`."""

    min_nights: int
    max_nights: int

    def __post_init__(self) -> None:
        if self.min_nights < 0:
            raise ValueError(f"min_nights can't be negative, got {self.min_nights}")
        if self.max_nights < self.min_nights:
            raise ValueError(
                f"max_nights ({self.max_nights}) is less than min_nights ({self.min_nights})"
            )

    def __contains__(self, nights: int) -> bool:
        return self.min_nights <= nights <= self.max_nights


@dataclass(frozen=True, slots=True)
class ExactDates:
    """A specific outbound departure date and, optionally, a specific return date."""

    outbound: date
    inbound: date | None = None

    def __post_init__(self) -> None:
        if self.inbound is not None and self.inbound < self.outbound:
            raise ValueError(
                f"Return date {self.inbound.isoformat()} is before outbound date "
                f"{self.outbound.isoformat()}"
            )

    @property
    def is_round_trip(self) -> bool:
        return self.inbound is not None


@dataclass(frozen=True, slots=True)
class FlexibleDates:
    """An outbound departure window, plus optional limits on the return.

    The trip is a round trip if either `stay` or `return_window` is given, and one-way
    if both are None. `return_window` limits the return's departure date.
    """

    depart: DateWindow
    stay: StayRange | None = None
    return_window: DateWindow | None = None

    def __post_init__(self) -> None:
        if self.return_window is not None:
            earliest = self.depart.start + timedelta(
                days=self.stay.min_nights if self.stay is not None else 0
            )
            if self.return_window.end < earliest:
                raise ValueError(
                    f"Return window ends {self.return_window.end.isoformat()}, before the "
                    f"earliest possible return {earliest.isoformat()}"
                )

    @property
    def is_round_trip(self) -> bool:
        return self.stay is not None or self.return_window is not None


# Arriving before the target and leaving after it means at least two nights.
BE_THERE_MIN_NIGHTS = 2


@dataclass(frozen=True, slots=True)
class BeThereOn:
    """Be at the destination for the whole of `target`, without flying that day.

    The outbound must arrive before `target` and the return must depart after it, both
    as local dates at the destination. A return is always required, so unlike the other
    modes `stay=None` means "no limit on nights", not one-way.
    """

    target: date
    stay: StayRange | None = None
    depart: DateWindow | None = None

    def __post_init__(self) -> None:
        if self.stay is not None and self.stay.max_nights < BE_THERE_MIN_NIGHTS:
            raise ValueError(
                f"Being there on {self.target.isoformat()} needs at least "
                f"{BE_THERE_MIN_NIGHTS} nights, but max_nights is {self.stay.max_nights}"
            )
        if self.depart is not None and self.depart.start >= self.target:
            raise ValueError(
                f"Departure window starts {self.depart.start.isoformat()}, which is not "
                f"before the target date {self.target.isoformat()}"
            )

    @property
    def latest_arrival_date(self) -> date:
        return self.target - timedelta(days=1)

    @property
    def earliest_return_date(self) -> date:
        return self.target + timedelta(days=1)

    @property
    def is_round_trip(self) -> bool:
        return True


DateMode = ExactDates | FlexibleDates | BeThereOn


# --- The request -----------------------------------------------------------------------

# Raise once hub selection and the lookup budget can handle more connections.
MAX_SUPPORTED_STOPS = 1

DEFAULT_MIN_CONNECTION = timedelta(hours=2)
SHORTEST_ALLOWED_CONNECTION = timedelta(minutes=30)
DEFAULT_MAX_LAYOVER = timedelta(hours=24)


DEFAULT_MAX_RESULTS = 20
MAX_RESULTS_LIMIT = 100


@dataclass(frozen=True, slots=True, kw_only=True)
class SearchRequest:
    """Everything needed to run a search; see the module docstring for what isn't here."""

    origin: tuple[LocationSpec, ...]
    destination: tuple[LocationSpec, ...]
    dates: DateMode
    max_stops: int = 0
    min_connection: timedelta = DEFAULT_MIN_CONNECTION
    max_layover: timedelta = DEFAULT_MAX_LAYOVER
    allow_different_return_airport: bool = True
    currency: str = BASE_CURRENCY
    max_results: int = DEFAULT_MAX_RESULTS

    def __post_init__(self) -> None:
        object.__setattr__(self, "origin", _location_set(tuple(self.origin), "Origin"))
        object.__setattr__(
            self, "destination", _location_set(tuple(self.destination), "Destination")
        )
        if set(self.origin) == set(self.destination):
            raise ValueError("Origin and destination are the same")

        if not 0 <= self.max_stops <= MAX_SUPPORTED_STOPS:
            raise ValueError(
                f"max_stops must be between 0 and {MAX_SUPPORTED_STOPS}, got {self.max_stops}"
            )
        if self.min_connection < SHORTEST_ALLOWED_CONNECTION:
            raise ValueError(
                f"min_connection must be at least {SHORTEST_ALLOWED_CONNECTION}, "
                f"got {self.min_connection}"
            )
        if self.max_layover <= self.min_connection:
            raise ValueError(
                f"max_layover ({self.max_layover}) must be longer than "
                f"min_connection ({self.min_connection})"
            )
        if self.currency != BASE_CURRENCY:
            raise ValueError(f"Only {BASE_CURRENCY} is supported for now, got {self.currency!r}")
        if not 1 <= self.max_results <= MAX_RESULTS_LIMIT:
            raise ValueError(
                f"max_results must be between 1 and {MAX_RESULTS_LIMIT}, got {self.max_results}"
            )

    @property
    def is_round_trip(self) -> bool:
        return self.dates.is_round_trip
