"""The shared HTTP client. Every request to a source goes through here.

It enforces these politeness rules, so adapters do not have to:

- an identifying User-Agent
- a per host delay with jitter, and one request per host at a time
- 10 s connect and 30 s read timeouts
- 3 retries on network errors, 429 and 5xx, with exponential backoff that
  honors Retry-After
- robots.txt, fetched once per host for the life of the client
- conditional requests with If-None-Match and If-Modified-Since
- a stop on 401, 403, a CAPTCHA page or a login wall

Create one client per source run. Once it sees a block it refuses every
later request, which stops the source for the day.
"""

import os
import time
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urljoin, urlsplit

import requests
import structlog
from requests.structures import CaseInsensitiveDict

from jobtracker_ingest import __version__
from jobtracker_ingest.core.blocks import block_reason, is_login_url
from jobtracker_ingest.core.ratelimit import HostRateLimiter
from jobtracker_ingest.core.robots import RobotsRules, robots_url

CONNECT_TIMEOUT_SECONDS = 10.0
READ_TIMEOUT_SECONDS = 30.0
MAX_RETRIES = 3
BACKOFF_BASE_SECONDS = 2.0
MAX_BACKOFF_SECONDS = 60.0
# If a server asks us to wait longer than this we give up for now and let
# the Airflow task retry later
MAX_RETRY_AFTER_SECONDS = 300.0
MAX_REDIRECTS = 5

NETWORK_ERRORS = (requests.ConnectionError, requests.Timeout)


class HttpError(Exception):
    def __init__(self, url: str, message: str, status: int | None = None) -> None:
        super().__init__(f"{message}: {url}")
        self.url = url
        self.status = status


class BlockedError(HttpError):
    """The site blocked us. Stop the source for the day and alert."""


class DisallowedByRobots(HttpError):
    """robots.txt does not allow this URL. Skip it."""


class RetriesExhausted(HttpError):
    """Network errors, 429 or 5xx kept coming back after every retry."""


class HttpStatusError(HttpError):
    """A 4xx we do not retry, for example 404 on a posting that is gone."""


class TooManyRedirects(HttpError):
    pass


def build_user_agent(contact: str, repo_url: str, version: str = __version__) -> str:
    return f"GreekTechJobsTracker/{version} (+{repo_url}; {contact})"


def user_agent_from_env(contact: str | None = None) -> str:
    """User-Agent from the SCRAPER_* variables. A registry contact wins."""
    return build_user_agent(
        contact=contact or os.environ["SCRAPER_CONTACT_EMAIL"],
        repo_url=os.environ["SCRAPER_REPO_URL"],
        version=os.environ.get("SCRAPER_USER_AGENT_VERSION", __version__),
    )


@dataclass(frozen=True)
class FetchResult:
    url: str
    status: int
    content: bytes
    headers: CaseInsensitiveDict[str]
    encoding: str | None
    fetched_at: datetime
    elapsed_seconds: float
    attempts: int

    @property
    def not_modified(self) -> bool:
        return self.status == 304

    @property
    def etag(self) -> str | None:
        return self.headers.get("ETag")

    @property
    def last_modified(self) -> str | None:
        return self.headers.get("Last-Modified")

    @property
    def content_type(self) -> str:
        return self.headers.get("Content-Type", "")

    @property
    def text(self) -> str:
        return self.content.decode(self.encoding or "utf-8", errors="replace")


@dataclass
class ClientStats:
    """Counts for the run manifest and the alert messages."""

    requests: int = 0
    retries: int = 0
    network_errors: int = 0
    bytes_received: int = 0
    robots_skipped: int = 0
    status_counts: Counter[int] = field(default_factory=Counter)

    def as_dict(self) -> dict[str, Any]:
        return {
            "requests": self.requests,
            "retries": self.retries,
            "network_errors": self.network_errors,
            "bytes_received": self.bytes_received,
            "robots_skipped": self.robots_skipped,
            "status_counts": {str(k): v for k, v in sorted(self.status_counts.items())},
        }


def parse_retry_after(value: str | None, now: datetime | None = None) -> float | None:
    """Seconds to wait from a Retry-After header, in seconds or as an HTTP date."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - (now or datetime.now(UTC))).total_seconds())


def host_of(url: str) -> str:
    return urlsplit(url).netloc.lower()


class PoliteClient:
    def __init__(
        self,
        user_agent: str,
        min_delay_seconds: float,
        session: requests.Session | None = None,
        limiter: HostRateLimiter | None = None,
        logger: Any = None,
        sleep: Callable[[float], None] = time.sleep,
        max_retries: int = MAX_RETRIES,
    ) -> None:
        self.user_agent = user_agent
        self.session = session or requests.Session()
        self.limiter = limiter or HostRateLimiter(min_delay_seconds, sleep=sleep)
        self.log = logger or structlog.get_logger(__name__)
        self.stats = ClientStats()
        self.blocked: BlockedError | None = None
        self._sleep = sleep
        self._max_retries = max_retries
        self._robots: dict[str, RobotsRules] = {}

    def get(
        self,
        url: str,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> FetchResult:
        """Fetch one URL politely.

        Pass the ETag and Last-Modified from the previous fetch to make a
        conditional request; a 304 comes back with not_modified set.
        """
        extra = dict(headers or {})
        if etag:
            extra["If-None-Match"] = etag
        if last_modified:
            extra["If-Modified-Since"] = last_modified

        result = self._fetch(url, extra, check_robots=True)
        if result.status >= 400:
            raise HttpStatusError(url, f"HTTP {result.status}", result.status)
        return result

    def _fetch(self, url: str, headers: dict[str, str], check_robots: bool) -> FetchResult:
        if self.blocked:
            raise BlockedError(url, f"source already blocked ({self.blocked})")

        current = url
        for _ in range(MAX_REDIRECTS + 1):
            if check_robots:
                self._check_robots(current)
            result = self._send_with_retries(current, headers)
            location = result.headers.get("Location")
            if result.status not in (301, 302, 303, 307, 308) or not location:
                return result

            target = urljoin(current, location)
            if is_login_url(target):
                self._block(current, f"redirected to a login page ({target})", result.status)
            self.log.info("http_redirect", url=current, location=target, status=result.status)
            current = target
        raise TooManyRedirects(url, f"more than {MAX_REDIRECTS} redirects")

    def _send_with_retries(self, url: str, headers: dict[str, str]) -> FetchResult:
        host = host_of(url)
        all_headers = {"User-Agent": self.user_agent, **headers}
        attempt = 0
        while True:
            attempt += 1
            started = time.perf_counter()
            try:
                with self.limiter.slot(host):
                    self.stats.requests += 1
                    response = self.session.get(
                        url,
                        headers=all_headers,
                        timeout=(CONNECT_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS),
                        allow_redirects=False,
                    )
                    content = response.content
            except NETWORK_ERRORS as err:
                self.stats.network_errors += 1
                if attempt > self._max_retries:
                    raise RetriesExhausted(
                        url, f"{type(err).__name__} after {attempt} tries"
                    ) from err
                self._wait_before_retry(url, attempt, None, type(err).__name__)
                continue

            elapsed = time.perf_counter() - started
            self.stats.status_counts[response.status_code] += 1
            self.stats.bytes_received += len(content)
            self.log.info(
                "http_request",
                url=url,
                status=response.status_code,
                bytes=len(content),
                elapsed_ms=round(elapsed * 1000),
                attempt=attempt,
            )

            content_type = response.headers.get("Content-Type", "")
            reason = block_reason(response.status_code, content_type, content)
            if reason:
                self._block(url, reason, response.status_code)

            if response.status_code == 429 or response.status_code >= 500:
                if attempt > self._max_retries:
                    raise RetriesExhausted(
                        url,
                        f"HTTP {response.status_code} after {attempt} tries",
                        response.status_code,
                    )
                retry_after = parse_retry_after(response.headers.get("Retry-After"))
                if retry_after is not None and retry_after > MAX_RETRY_AFTER_SECONDS:
                    raise RetriesExhausted(
                        url, f"server asked to wait {retry_after:.0f} s", response.status_code
                    )
                self._wait_before_retry(url, attempt, retry_after, f"HTTP {response.status_code}")
                continue

            return FetchResult(
                url=url,
                status=response.status_code,
                content=content,
                headers=response.headers,
                encoding=response.encoding,
                fetched_at=datetime.now(UTC),
                elapsed_seconds=elapsed,
                attempts=attempt,
            )

    def _wait_before_retry(
        self, url: str, attempt: int, retry_after: float | None, cause: str
    ) -> None:
        backoff = min(BACKOFF_BASE_SECONDS * 2 ** (attempt - 1), MAX_BACKOFF_SECONDS)
        wait = max(backoff, retry_after or 0.0)
        self.stats.retries += 1
        self.log.warning("http_retry", url=url, attempt=attempt, cause=cause, wait_s=wait)
        self._sleep(wait)

    def _check_robots(self, url: str) -> None:
        rules = self._robots_for(url)
        if not rules.can_fetch(url):
            self.stats.robots_skipped += 1
            self.log.warning("robots_disallowed", url=url)
            raise DisallowedByRobots(url, "disallowed by robots.txt")

    def _robots_for(self, url: str) -> RobotsRules:
        key = robots_url(url)
        if key in self._robots:
            return self._robots[key]

        try:
            result = self._fetch(key, {}, check_robots=False)
            rules = RobotsRules.from_response(result.status, result.text)
        except (RetriesExhausted, TooManyRedirects) as err:
            rules = RobotsRules.from_response(None)
            self.log.warning("robots_unavailable", url=key, error=str(err))

        self._robots[key] = rules
        if rules.crawl_delay:
            self.limiter.set_host_delay(host_of(url), rules.crawl_delay)
        self.log.info("robots_loaded", url=key, rules=rules.reason, crawl_delay=rules.crawl_delay)
        return rules

    def _block(self, url: str, reason: str, status: int | None) -> None:
        self.blocked = BlockedError(url, reason, status)
        self.log.error("source_blocked", url=url, reason=reason, status=status)
        raise self.blocked
