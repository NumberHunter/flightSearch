import json
from collections.abc import AsyncIterator, Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from flightsearch.domain import Provider
from flightsearch.providers import (
    FlightPriceProvider,
    ProviderRequestError,
    ProviderResponseError,
    ProviderUnavailableError,
    RateLimiter,
)
from flightsearch.providers.ryanair import RyanairProvider
from flightsearch.providers.ryanair.provider import month_starts
from flightsearch.reference import InMemoryAirportRepository

FIXTURES = Path(__file__).parent / "fixtures" / "ryanair"
AIRPORTS = InMemoryAirportRepository.load_default()
SOF = AIRPORTS.get("SOF")
BGY = AIRPORTS.get("BGY")
URL = "https://www.ryanair.com/api/farfnd/v4/oneWayFares/SOF/BGY/cheapestPerDay"
NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)

DECEMBER = json.loads(
    (FIXTURES / "cheapest_per_day_SOF_BGY_2026-12.json").read_text(encoding="utf-8")
)
NOVEMBER = {
    "outbound": {
        "fares": [
            {
                "day": f"2026-11-{day}",
                "departureDate": f"2026-11-{day}T11:30:00",
                "arrivalDate": f"2026-11-{day}T12:35:00",
                "price": {"value": 20.99, "currencyCode": "EUR"},
                "soldOut": False,
                "unavailable": False,
            }
            for day in (28, 29, 30)
        ]
    }
}
BY_MONTH = {"2026-11-01": NOVEMBER, "2026-12-01": DECEMBER}


def by_month(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json=BY_MONTH[request.url.params["outboundMonthOfDate"]])


@pytest.fixture
async def ryanair() -> AsyncIterator[RyanairProvider]:
    async with httpx.AsyncClient() as client:
        yield RyanairProvider(
            client, rate_limiter=RateLimiter(0), backoff_seconds=0, clock=lambda: NOW
        )


def test_satisfies_the_provider_interface() -> None:
    provider: FlightPriceProvider = RyanairProvider(httpx.AsyncClient())
    assert provider.provider is Provider.RYANAIR


# --- get_daily_fares -------------------------------------------------------------------


async def test_fetches_one_month(ryanair: RyanairProvider, respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(URL).mock(side_effect=by_month)

    legs = await ryanair.get_daily_fares(SOF, BGY, date(2026, 12, 1), date(2026, 12, 31))

    assert len(legs) == 24
    assert route.call_count == 1
    params = route.calls.last.request.url.params
    assert params["outboundMonthOfDate"] == "2026-12-01"
    assert params["currency"] == "EUR"
    assert all(leg.fetched_at == NOW for leg in legs)


async def test_spans_months_and_keeps_only_the_requested_days(
    ryanair: RyanairProvider, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.get(URL).mock(side_effect=by_month)

    legs = await ryanair.get_daily_fares(SOF, BGY, date(2026, 11, 29), date(2026, 12, 3))

    assert route.call_count == 2
    # 29 and 30 Nov, then 1 and 3 Dec (no flight on the 2nd).
    assert [leg.departure.date() for leg in legs] == [
        date(2026, 11, 29),
        date(2026, 11, 30),
        date(2026, 12, 1),
        date(2026, 12, 3),
    ]


async def test_rejects_a_backwards_date_range(ryanair: RyanairProvider) -> None:
    with pytest.raises(ValueError, match="before"):
        await ryanair.get_daily_fares(SOF, BGY, date(2026, 12, 3), date(2026, 12, 1))


# --- Failures --------------------------------------------------------------------------


def responses(*items: httpx.Response | Exception) -> Callable[[httpx.Request], Any]:
    queue = list(items)

    def respond(request: httpx.Request) -> httpx.Response:
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    return respond


async def test_retries_server_errors_and_network_failures(
    ryanair: RyanairProvider, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.get(URL).mock(
        side_effect=responses(
            httpx.Response(503),
            httpx.ConnectError("connection refused"),
            httpx.Response(200, json=DECEMBER),
        )
    )

    legs = await ryanair.get_daily_fares(SOF, BGY, date(2026, 12, 1), date(2026, 12, 31))

    assert len(legs) == 24
    assert route.call_count == 3


async def test_gives_up_after_the_last_attempt(
    ryanair: RyanairProvider, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.get(URL).mock(return_value=httpx.Response(429))

    with pytest.raises(ProviderUnavailableError, match="HTTP 429"):
        await ryanair.get_daily_fares(SOF, BGY, date(2026, 12, 1), date(2026, 12, 31))
    assert route.call_count == 3


async def test_does_not_retry_a_rejected_request(
    ryanair: RyanairProvider, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.get(URL).mock(return_value=httpx.Response(400))

    with pytest.raises(ProviderRequestError, match="HTTP 400"):
        await ryanair.get_daily_fares(SOF, BGY, date(2026, 12, 1), date(2026, 12, 31))
    assert route.call_count == 1


async def test_rejects_a_response_that_isnt_json(
    ryanair: RyanairProvider, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(URL).mock(return_value=httpx.Response(200, text="<html>Blocked</html>"))

    with pytest.raises(ProviderResponseError, match="JSON"):
        await ryanair.get_daily_fares(SOF, BGY, date(2026, 12, 1), date(2026, 12, 31))


# --- get_flights_for_day and get_routes ------------------------------------------------

SCHEDULE_URL = "https://www.ryanair.com/api/timtbl/3/schedules/SOF/BGY/years/2026/months/12"
ROUTES_URL = "https://www.ryanair.com/api/views/locate/searchWidget/routes/en/airport/SOF"


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


async def test_flights_for_day_returns_only_that_days_flights(
    ryanair: RyanairProvider, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.get(SCHEDULE_URL).mock(
        return_value=httpx.Response(200, json=fixture("schedule_SOF_BGY_2026-12.json"))
    )

    legs = await ryanair.get_flights_for_day(SOF, BGY, date(2026, 12, 14))

    assert route.call_count == 1
    assert [leg.flight_number for leg in legs] == ["FR3438", "FR3401"]
    assert all(leg.departure.date() == date(2026, 12, 14) for leg in legs)
    assert all(leg.fetched_at == NOW for leg in legs)


async def test_flights_for_a_day_without_flights_is_empty(
    ryanair: RyanairProvider, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(SCHEDULE_URL).mock(
        return_value=httpx.Response(200, json=fixture("schedule_SOF_BGY_2026-12.json"))
    )
    assert await ryanair.get_flights_for_day(SOF, BGY, date(2026, 12, 25)) == []


async def test_routes(ryanair: RyanairProvider, respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(ROUTES_URL).mock(
        return_value=httpx.Response(200, json=fixture("routes_SOF.json"))
    )

    codes = await ryanair.get_routes(SOF)

    assert route.call_count == 1
    assert len(codes) == 46
    assert "BGY" in codes


async def test_routes_are_retried_like_every_other_request(
    ryanair: RyanairProvider, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.get(ROUTES_URL).mock(
        side_effect=responses(
            httpx.Response(502), httpx.Response(200, json=fixture("routes_VAR.json"))
        )
    )
    assert await ryanair.get_routes(SOF) == ["FMM", "KRK", "KTW"]
    assert route.call_count == 2


# --- Client ownership ------------------------------------------------------------------


async def test_closes_a_client_it_created() -> None:
    async with RyanairProvider() as ryanair:
        client = ryanair._client
    assert client.is_closed


async def test_leaves_a_shared_client_open() -> None:
    async with httpx.AsyncClient() as client:
        async with RyanairProvider(client):
            pass
        assert not client.is_closed


# --- month_starts ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("date_from", "date_to", "expected"),
    [
        (date(2026, 12, 5), date(2026, 12, 20), [date(2026, 12, 1)]),
        (date(2026, 11, 29), date(2026, 12, 3), [date(2026, 11, 1), date(2026, 12, 1)]),
        (
            date(2026, 12, 31),
            date(2027, 2, 1),
            [date(2026, 12, 1), date(2027, 1, 1), date(2027, 2, 1)],
        ),
    ],
)
def test_month_starts(date_from: date, date_to: date, expected: list[date]) -> None:
    assert month_starts(date_from, date_to) == expected


# --- RateLimiter -----------------------------------------------------------------------


class FakeTime:
    def __init__(self) -> None:
        self.now = 100.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


async def test_rate_limiter_spaces_out_calls() -> None:
    time = FakeTime()
    limiter = RateLimiter(1.0, clock=time.clock, sleep=time.sleep)

    await limiter.wait()  # first call goes straight through
    time.now += 0.25
    await limiter.wait()  # 0.25 s later: waits the remaining 0.75 s
    time.now += 5
    await limiter.wait()  # long after: no wait

    assert time.sleeps == [0.75]


def test_rate_limiter_rejects_a_negative_interval() -> None:
    with pytest.raises(ValueError, match="negative"):
        RateLimiter(-1)
