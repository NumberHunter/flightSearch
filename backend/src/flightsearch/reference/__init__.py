"""Reference data (airports; later airport groups and the route network)."""

from flightsearch.reference.airports import (
    AirportDataError,
    AirportRepository,
    InMemoryAirportRepository,
    UnknownAirportError,
)

__all__ = [
    "AirportDataError",
    "AirportRepository",
    "InMemoryAirportRepository",
    "UnknownAirportError",
]
