"""The interface every airline adapter implements, and the errors adapters raise."""

from __future__ import annotations

from datetime import date
from typing import Protocol

from flightsearch.domain import BASE_CURRENCY, Airport, Leg, Provider


class ProviderError(Exception):
    """Base class for a provider lookup that failed."""

    def __init__(self, provider: Provider, message: str) -> None:
        super().__init__(f"{provider}: {message}")
        self.provider = provider


class ProviderUnavailableError(ProviderError):
    """Network failure, server error or rate limiting that persisted through every retry."""


class ProviderRequestError(ProviderError):
    """The provider rejected the request (a 4xx other than 429). Retrying won't help."""


class ProviderResponseError(ProviderError):
    """The response wasn't in the expected shape: the endpoint has probably changed."""


class FlightPriceProvider(Protocol):
    """One airline's data, behind the same interface for every airline.

    Airports are passed as `Airport` objects, not codes, because providers return local
    times without an offset and need each airport's time zone to interpret them.
    """

    @property
    def provider(self) -> Provider: ...

    async def get_daily_fares(
        self,
        origin: Airport,
        destination: Airport,
        date_from: date,
        date_to: date,
        currency: str = BASE_CURRENCY,
    ) -> list[Leg]:
        """The cheapest flight of each day departing between the two dates, inclusive."""
        ...

    async def get_flights_for_day(
        self, origin: Airport, destination: Airport, day: date
    ) -> list[Leg]:
        """Every departure that day; prices may be unknown (`PriceConfidence.CHECK`)."""
        ...

    async def get_routes(self, origin: Airport) -> list[str]:
        """IATA codes of the destinations served from `origin`."""
        ...
