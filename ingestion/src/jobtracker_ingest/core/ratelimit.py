"""Per host rate limiting for the shared HTTP client.

Every host gets at least MIN_DELAY_SECONDS between two requests, plus a
random jitter, and only one request in flight at a time.
"""

import random
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

# The minimum delay per host. A registry value below this is raised to it.
MIN_DELAY_SECONDS = 3.0
MAX_JITTER_SECONDS = 1.0


class HostRateLimiter:
    """Spaces out requests per host.

    The delay is counted from the end of the previous request, so a slow
    response never shortens the gap. Clock, sleep and random source can be
    swapped in tests.
    """

    def __init__(
        self,
        min_delay_seconds: float = MIN_DELAY_SECONDS,
        max_jitter_seconds: float = MAX_JITTER_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
    ) -> None:
        self.min_delay_seconds = max(min_delay_seconds, MIN_DELAY_SECONDS)
        self.max_jitter_seconds = max_jitter_seconds
        self._clock = clock
        self._sleep = sleep
        self._rng = rng or random.Random()
        self._host_delays: dict[str, float] = {}
        self._last_done: dict[str, float] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def set_host_delay(self, host: str, seconds: float) -> None:
        """Use a longer delay for one host, for example a robots.txt Crawl-delay."""
        self._host_delays[host] = max(seconds, self.min_delay_seconds)

    def delay_for(self, host: str) -> float:
        return self._host_delays.get(host, self.min_delay_seconds)

    @contextmanager
    def slot(self, host: str) -> Iterator[float]:
        """Wait for our turn on a host, then hold it until the request is done.

        Yields the number of seconds we waited.
        """
        with self._lock_for(host):
            waited = self._wait_turn(host)
            try:
                yield waited
            finally:
                self._last_done[host] = self._clock()

    def _lock_for(self, host: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(host, threading.Lock())

    def _wait_turn(self, host: str) -> float:
        last = self._last_done.get(host)
        if last is None:
            return 0.0
        gap = self.delay_for(host) + self._rng.uniform(0, self.max_jitter_seconds)
        wait = last + gap - self._clock()
        if wait <= 0:
            return 0.0
        self._sleep(wait)
        return wait
