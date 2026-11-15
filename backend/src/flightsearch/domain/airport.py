"""Airport: the reference entity every leg, location set and route refers to."""

from __future__ import annotations

import math
from dataclasses import dataclass
from zoneinfo import ZoneInfo

EARTH_RADIUS_KM = 6371.0088  # mean Earth radius


def normalize_iata(code: str) -> str:
    """Return an IATA airport code in canonical form ('var' -> 'VAR').

    Raises ValueError if it isn't three ASCII letters.
    """
    normalized = code.strip().upper()
    if len(normalized) != 3 or not (normalized.isascii() and normalized.isalpha()):
        raise ValueError(f"Invalid IATA airport code: {code!r}")
    return normalized


def great_circle_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine distance between two points, in kilometres."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


@dataclass(frozen=True, slots=True)
class Airport:
    """An airport with the data needed for search: identity, location and time zone.

    `timezone` is what turns the airlines' local, offset-less times into real instants,
    so durations and connections across time zones are computed correctly.
    """

    iata: str
    name: str
    city: str
    country_code: str
    latitude: float
    longitude: float
    timezone: ZoneInfo

    def __post_init__(self) -> None:
        if self.iata != normalize_iata(self.iata):
            raise ValueError(f"IATA code must be canonical (3 upper-case letters): {self.iata!r}")
        if not -90 <= self.latitude <= 90:
            raise ValueError(f"Latitude out of range for {self.iata}: {self.latitude}")
        if not -180 <= self.longitude <= 180:
            raise ValueError(f"Longitude out of range for {self.iata}: {self.longitude}")
        if len(self.country_code) != 2:
            raise ValueError(f"Country code must be ISO 3166-1 alpha-2: {self.country_code!r}")

    def distance_km(self, other: Airport) -> float:
        return great_circle_km(self.latitude, self.longitude, other.latitude, other.longitude)
