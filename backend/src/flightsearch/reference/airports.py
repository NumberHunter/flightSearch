"""Airport reference data: lookup by code and radius search.

`AirportRepository` is the interface the rest of the app depends on.
`InMemoryAirportRepository` loads the committed `data/airports.csv` (built by
`scripts/build_airports.py`). A Postgres-backed implementation can replace it later
without touching callers. Airport data is small and rarely changes, so even then it
is likely to be loaded into memory at startup, which is why this interface is sync.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable, Iterator
from importlib import resources
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flightsearch.domain.airport import Airport, great_circle_km, normalize_iata


class UnknownAirportError(KeyError):
    """Raised when an airport code is not in the reference data."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code

    def __str__(self) -> str:
        return f"Unknown airport: {self.code!r}"


class AirportDataError(ValueError):
    """Raised when the airport reference file is malformed."""


class AirportRepository(Protocol):
    def get(self, code: str) -> Airport:
        """Return the airport for an IATA code (case-insensitive).

        Raises UnknownAirportError if the code is not known.
        """
        ...

    def find(self, code: str) -> Airport | None:
        """Like `get`, but returns None for unknown or malformed codes."""
        ...

    def all(self) -> list[Airport]:
        """Every airport, sorted by IATA code."""
        ...

    def within_radius(self, latitude: float, longitude: float, radius_km: float) -> list[Airport]:
        """Airports within `radius_km` of a point, nearest first."""
        ...


class InMemoryAirportRepository:
    def __init__(self, airports: Iterable[Airport]) -> None:
        by_iata: dict[str, Airport] = {}
        for airport in airports:
            if airport.iata in by_iata:
                raise AirportDataError(f"Duplicate airport code: {airport.iata}")
            by_iata[airport.iata] = airport
        self._by_iata = dict(sorted(by_iata.items()))

    @classmethod
    def from_csv(cls, path: Path) -> InMemoryAirportRepository:
        with path.open(encoding="utf-8", newline="") as f:
            return cls(_parse_rows(csv.DictReader(f), source=str(path)))

    @classmethod
    def load_default(cls) -> InMemoryAirportRepository:
        """Load the airport file shipped inside the package."""
        data = resources.files("flightsearch").joinpath("data", "airports.csv")
        with resources.as_file(data) as path:
            return cls.from_csv(path)

    def get(self, code: str) -> Airport:
        airport = self.find(code)
        if airport is None:
            raise UnknownAirportError(code)
        return airport

    def find(self, code: str) -> Airport | None:
        try:
            return self._by_iata.get(normalize_iata(code))
        except ValueError:
            return None

    def all(self) -> list[Airport]:
        return list(self._by_iata.values())

    def within_radius(self, latitude: float, longitude: float, radius_km: float) -> list[Airport]:
        if radius_km < 0:
            raise ValueError(f"radius_km must be non-negative, got {radius_km}")
        with_distance = [
            (great_circle_km(latitude, longitude, a.latitude, a.longitude), a)
            for a in self._by_iata.values()
        ]
        return [a for d, a in sorted(with_distance, key=lambda x: x[0]) if d <= radius_km]


def _parse_rows(rows: Iterable[dict[str, str]], source: str) -> Iterator[Airport]:
    # Line 1 is the header, so data starts on line 2.
    for line, row in enumerate(rows, start=2):
        # csv.DictReader fills missing trailing columns with None.
        missing = [column for column, value in row.items() if value is None]
        if missing:
            raise AirportDataError(f"{source}, line {line}: missing columns {missing}")
        try:
            yield Airport(
                iata=row["iata"],
                name=row["name"],
                city=row["city"],
                country_code=row["country_code"],
                latitude=float(row["latitude"]),
                longitude=float(row["longitude"]),
                timezone=ZoneInfo(row["timezone"]),
            )
        except (KeyError, ValueError, ZoneInfoNotFoundError) as exc:
            raise AirportDataError(f"{source}, line {line}: {exc}") from exc
