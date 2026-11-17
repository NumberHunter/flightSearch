"""Spacing out requests to one provider, so we stay polite to unofficial endpoints."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable


class RateLimiter:
    """Lets calls through at most once every `interval` seconds, across concurrent callers.

    In-process only, which is enough while one process makes all the requests. Once
    several workers share a provider, this moves to Redis behind the same `wait()` call.
    `clock` and `sleep` are injectable so tests can run without real waiting.
    """

    def __init__(
        self,
        interval: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if interval < 0:
            raise ValueError(f"Rate limit interval can't be negative, got {interval}")
        self._interval = interval
        self._clock = clock
        self._sleep = sleep
        self._lock = asyncio.Lock()
        self._next_allowed: float | None = None

    async def wait(self) -> None:
        """Return once the caller is allowed to make its request."""
        async with self._lock:
            now = self._clock()
            if self._next_allowed is not None and now < self._next_allowed:
                await self._sleep(self._next_allowed - now)
                now = self._next_allowed
            self._next_allowed = now + self._interval
