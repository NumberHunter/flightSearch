"""RyanairProvider: Ryanair's unofficial website endpoints behind `FlightPriceProvider`.

- `get_daily_fares`: the fare calendar, the cheapest flight of each day with its price.
- `get_flights_for_day`: the timetable, every flight with times but no prices.
- `get_routes`: the booking widget's list of destinations from an airport.

Each endpoint has a saved real response in `tests/fixtures/ryanair/`.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from types import TracebackType
from typing import Self

import httpx
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from flightsearch.domain import BASE_CURRENCY, Airport, Leg, Provider
from flightsearch.providers.base import (
    ProviderRequestError,
    ProviderResponseError,
    ProviderUnavailableError,
)
from flightsearch.providers.rate_limit import RateLimiter
from flightsearch.providers.ryanair.parsing import (
    parse_cheapest_per_day,
    parse_routes,
    parse_schedule,
)

BASE_URL = "https://www.ryanair.com"
CHEAPEST_PER_DAY_URL = BASE_URL + "/api/farfnd/v4/oneWayFares/{origin}/{destination}/cheapestPerDay"
SCHEDULE_URL = (
    BASE_URL + "/api/timtbl/3/schedules/{origin}/{destination}/years/{year}/months/{month}"
)
ROUTES_URL = BASE_URL + "/api/views/locate/searchWidget/routes/en/airport/{origin}"

DEFAULT_HEADERS = {"Accept": "application/json", "User-Agent": "flightsearch/0.1"}
DEFAULT_TIMEOUT = httpx.Timeout(15.0)
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})

# About one request per second, as the project brief asks.
DEFAULT_REQUEST_INTERVAL = 0.1


class _TransientError(Exception):
    """A failure worth retrying: network trouble, a server error or rate limiting."""


def month_starts(date_from: date, date_to: date) -> list[date]:
    """The first day of every month that the inclusive range touches
    (We use these to search the Ryanair API using 1 request per month)."""
    if date_to < date_from:
        raise ValueError(
            f"date_to ({date_to.isoformat()}) is before date_from ({date_from.isoformat()})"
        )
    months: list[date] = []
    month = date_from.replace(day=1)
    while month <= date_to:
        months.append(month)
        if month.month == 12:
            month = month.replace(year=month.year + 1, month=1)
        else:
            month = month.replace(month=month.month + 1)
    return months


def _utc_now() -> datetime:
    return datetime.now(UTC)


class RyanairProvider:
    """Fetches Ryanair's price calendars, one month per request.

    Pass in an `httpx.AsyncClient` to share connections (or to test with a mock);
    otherwise the provider creates its own and closes it in `aclose()`. It can also be
    used as an async context manager: `async with RyanairProvider() as ryanair: ...`.
    """

    provider = Provider.RYANAIR

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        rate_limiter: RateLimiter | None = None,
        max_attempts: int = 3,
        backoff_seconds: float = 1.0,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if max_attempts < 1:
            raise ValueError(f"max_attempts must be at least 1, got {max_attempts}")
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(headers=DEFAULT_HEADERS, timeout=DEFAULT_TIMEOUT)
        self._rate_limiter = rate_limiter or RateLimiter(DEFAULT_REQUEST_INTERVAL)
        self._retrying = AsyncRetrying(
            stop=stop_after_attempt(max_attempts),
            wait=wait_exponential(multiplier=backoff_seconds, max=30),
            retry=retry_if_exception_type(_TransientError),
            reraise=True,
        )
        self._clock = clock

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def get_daily_fares(
        self,
        origin: Airport,
        destination: Airport,
        date_from: date,
        date_to: date,
        currency: str = BASE_CURRENCY,
    ) -> list[Leg]:
        """The cheapest flight of each day departing between the dates, inclusive.

        A route Ryanair doesn't fly returns an empty list, not an error: the endpoint
        answers it the same way as a month with no flights.
        """
        url = CHEAPEST_PER_DAY_URL.format(origin=origin.iata, destination=destination.iata)
        legs: list[Leg] = []
        for month in month_starts(date_from, date_to):
            payload = await self._get_json(
                url, {"outboundMonthOfDate": month.isoformat(), "currency": currency}
            )
            parsed = parse_cheapest_per_day(
                payload, origin=origin, destination=destination, fetched_at=self._clock()
            )
            legs.extend(leg for leg in parsed if date_from <= leg.departure.date() <= date_to)
        return legs

    async def get_flights_for_day(
        self, origin: Airport, destination: Airport, day: date
    ) -> list[Leg]:
        """Every scheduled flight departing on `day`, as CHECK legs without prices.

        The timetable is fetched for the whole month and filtered to the day. A route
        Ryanair doesn't fly returns an empty list.
        """
        url = SCHEDULE_URL.format(
            origin=origin.iata, destination=destination.iata, year=day.year, month=day.month
        )
        payload = await self._get_json(url, {})
        legs = parse_schedule(
            payload,
            origin=origin,
            destination=destination,
            year=day.year,
            month=day.month,
            fetched_at=self._clock(),
        )
        return [leg for leg in legs if leg.departure.date() == day]

    async def get_routes(self, origin: Airport) -> list[str]:
        """IATA codes of every airport Ryanair flies to directly from `origin`, sorted."""
        payload = await self._get_json(ROUTES_URL.format(origin=origin.iata), {})
        return parse_routes(payload)

    async def _get_json(self, url: str, params: dict[str, str]) -> object:
        """GET with rate limiting and retries, then decode the JSON body."""
        try:
            response: httpx.Response = await self._retrying(self._get_once, url, params)
        except _TransientError as exc:
            raise ProviderUnavailableError(self.provider, f"{url}: {exc}") from exc

        if response.is_error:
            raise ProviderRequestError(self.provider, f"{url} returned HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise ProviderResponseError(self.provider, f"{url} didn't return JSON") from exc

    async def _get_once(self, url: str, params: dict[str, str]) -> httpx.Response:
        await self._rate_limiter.wait()  # every attempt counts, retries included
        try:
            response = await self._client.get(url, params=params)
        except httpx.TransportError as exc:
            raise _TransientError(f"{type(exc).__name__}: {exc}") from exc
        if response.status_code in RETRYABLE_STATUS_CODES:
            raise _TransientError(f"HTTP {response.status_code}")
        return response
