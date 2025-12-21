from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
import requests
import responses
from conftest import FakeClock
from structlog.testing import capture_logs

from jobtracker_ingest.core.http import (
    BlockedError,
    DisallowedByRobots,
    HttpStatusError,
    PoliteClient,
    RetriesExhausted,
    TooManyRedirects,
    build_user_agent,
    parse_retry_after,
    user_agent_from_env,
)
from jobtracker_ingest.core.ratelimit import HostRateLimiter

UA = "GreekTechJobsTracker/0.1.0 (+https://github.com/example/jobs; maintainer@example.org)"
BASE = "https://jobs.example.org"
ROBOTS = f"{BASE}/robots.txt"
PAGE = f"{BASE}/jobs/1"


class Backoff:
    """Records retry sleeps without moving the rate limit clock."""

    def __init__(self) -> None:
        self.sleeps: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.sleeps.append(seconds)


@pytest.fixture
def rsps() -> Iterator[responses.RequestsMock]:
    with responses.RequestsMock() as mock:
        yield mock


@pytest.fixture
def backoff() -> Backoff:
    return Backoff()


@pytest.fixture
def client(clock: FakeClock, backoff: Backoff) -> PoliteClient:
    limiter = HostRateLimiter(3, max_jitter_seconds=0, clock=clock, sleep=clock.sleep)
    return PoliteClient(UA, 3, limiter=limiter, sleep=backoff)


def allow_robots(rsps: responses.RequestsMock, base: str = BASE, body: str = "") -> None:
    rsps.get(f"{base}/robots.txt", body=body, status=200)


def page_calls(rsps: responses.RequestsMock) -> list[str]:
    return [
        c.request.url or "" for c in rsps.calls if not str(c.request.url).endswith("robots.txt")
    ]


# User-Agent


def test_build_user_agent() -> None:
    ua = build_user_agent("me@example.org", "https://github.com/example/jobs", "1.2.3")
    assert ua == "GreekTechJobsTracker/1.2.3 (+https://github.com/example/jobs; me@example.org)"


def test_user_agent_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCRAPER_CONTACT_EMAIL", "env@example.org")
    monkeypatch.setenv("SCRAPER_REPO_URL", "https://github.com/example/jobs")
    monkeypatch.setenv("SCRAPER_USER_AGENT_VERSION", "0.9.0")
    assert user_agent_from_env() == (
        "GreekTechJobsTracker/0.9.0 (+https://github.com/example/jobs; env@example.org)"
    )
    assert "registry@example.org" in user_agent_from_env("registry@example.org")


def test_every_request_sends_the_user_agent_and_timeouts(
    rsps: responses.RequestsMock, client: PoliteClient
) -> None:
    allow_robots(rsps)
    rsps.get(PAGE, json={"id": 1})

    result = client.get(PAGE)

    assert result.status == 200
    assert result.content == b'{"id": 1}'
    for call in rsps.calls:
        assert call.request.headers["User-Agent"] == UA
        assert call.request.req_kwargs["timeout"] == (10.0, 30.0)  # type: ignore[attr-defined]


# Rate limit


def test_requests_to_one_host_are_spaced_out(
    rsps: responses.RequestsMock, client: PoliteClient, clock: FakeClock
) -> None:
    allow_robots(rsps)
    rsps.get(PAGE)
    rsps.get(f"{BASE}/jobs/2")

    client.get(PAGE)
    client.get(f"{BASE}/jobs/2")

    # robots.txt goes first with no wait, then each page waits the delay
    assert clock.sleeps == [3.0, 3.0]


def test_registry_delay_below_three_seconds_is_raised() -> None:
    client = PoliteClient(UA, min_delay_seconds=0.1)
    assert client.limiter.min_delay_seconds == 3.0


def test_crawl_delay_from_robots_is_honored(
    rsps: responses.RequestsMock, client: PoliteClient, clock: FakeClock
) -> None:
    allow_robots(rsps, body="User-agent: *\nCrawl-delay: 10\n")
    rsps.get(PAGE)
    rsps.get(f"{BASE}/jobs/2")

    client.get(PAGE)
    client.get(f"{BASE}/jobs/2")

    assert clock.sleeps == [10.0, 10.0]


# robots.txt


def test_robots_is_fetched_once_per_host(
    rsps: responses.RequestsMock, client: PoliteClient
) -> None:
    allow_robots(rsps)
    allow_robots(rsps, "https://api.example.org")
    for i in range(3):
        rsps.get(f"{BASE}/jobs/{i}")
    rsps.get("https://api.example.org/v1/jobs")

    for i in range(3):
        client.get(f"{BASE}/jobs/{i}")
    client.get("https://api.example.org/v1/jobs")

    robots_calls = [c for c in rsps.calls if str(c.request.url).endswith("/robots.txt")]
    assert len(robots_calls) == 2


def test_disallowed_url_is_skipped_and_logged(
    rsps: responses.RequestsMock, client: PoliteClient
) -> None:
    allow_robots(rsps, body="User-agent: *\nDisallow: /search\n")

    with capture_logs() as logs, pytest.raises(DisallowedByRobots):
        client.get(f"{BASE}/search?q=python")

    assert page_calls(rsps) == []
    assert client.stats.robots_skipped == 1
    assert {"event": "robots_disallowed", "url": f"{BASE}/search?q=python"}.items() <= next(
        log for log in logs if log["event"] == "robots_disallowed"
    ).items()


def test_missing_robots_allows_everything(
    rsps: responses.RequestsMock, client: PoliteClient
) -> None:
    rsps.get(ROBOTS, status=404)
    rsps.get(PAGE)
    assert client.get(PAGE).status == 200


def test_robots_server_error_disallows_everything(
    rsps: responses.RequestsMock, client: PoliteClient, backoff: Backoff
) -> None:
    rsps.get(ROBOTS, status=503)

    with pytest.raises(DisallowedByRobots):
        client.get(PAGE)

    # robots.txt was retried like any other request before giving up
    assert len(rsps.calls) == 4
    assert backoff.sleeps == [2.0, 4.0, 8.0]


def test_robots_network_error_disallows_everything(
    rsps: responses.RequestsMock, client: PoliteClient
) -> None:
    rsps.get(ROBOTS, body=requests.ConnectionError("refused"))
    with pytest.raises(DisallowedByRobots):
        client.get(PAGE)


def test_robots_forbidden_is_a_block(rsps: responses.RequestsMock, client: PoliteClient) -> None:
    rsps.get(ROBOTS, status=403)
    with pytest.raises(BlockedError):
        client.get(PAGE)
    assert client.blocked is not None


# Retries


def test_server_error_is_retried(
    rsps: responses.RequestsMock, client: PoliteClient, backoff: Backoff
) -> None:
    allow_robots(rsps)
    rsps.get(PAGE, status=503)
    rsps.get(PAGE, body="ok")

    result = client.get(PAGE)

    assert result.text == "ok"
    assert result.attempts == 2
    assert backoff.sleeps == [2.0]
    assert client.stats.retries == 1


def test_backoff_doubles_each_time(
    rsps: responses.RequestsMock, client: PoliteClient, backoff: Backoff
) -> None:
    allow_robots(rsps)
    for _ in range(3):
        rsps.get(PAGE, status=500)
    rsps.get(PAGE, body="ok")

    assert client.get(PAGE).attempts == 4
    assert backoff.sleeps == [2.0, 4.0, 8.0]


def test_gives_up_after_three_retries(rsps: responses.RequestsMock, client: PoliteClient) -> None:
    allow_robots(rsps)
    for _ in range(4):
        rsps.get(PAGE, status=502)

    with pytest.raises(RetriesExhausted) as err:
        client.get(PAGE)

    assert err.value.status == 502
    assert len(page_calls(rsps)) == 4


def test_network_error_is_retried(rsps: responses.RequestsMock, client: PoliteClient) -> None:
    allow_robots(rsps)
    rsps.get(PAGE, body=requests.ConnectTimeout("slow"))
    rsps.get(PAGE, body="ok")

    assert client.get(PAGE).text == "ok"
    assert client.stats.network_errors == 1


def test_network_error_gives_up_after_three_retries(
    rsps: responses.RequestsMock, client: PoliteClient
) -> None:
    allow_robots(rsps)
    for _ in range(4):
        rsps.get(PAGE, body=requests.ConnectionError("reset"))

    with pytest.raises(RetriesExhausted):
        client.get(PAGE)


def test_429_honors_retry_after(
    rsps: responses.RequestsMock, client: PoliteClient, backoff: Backoff
) -> None:
    allow_robots(rsps)
    rsps.get(PAGE, status=429, headers={"Retry-After": "20"})
    rsps.get(PAGE, body="ok")

    assert client.get(PAGE).text == "ok"
    assert backoff.sleeps == [20.0]


def test_short_retry_after_never_cuts_the_backoff(
    rsps: responses.RequestsMock, client: PoliteClient, backoff: Backoff
) -> None:
    allow_robots(rsps)
    rsps.get(PAGE, status=429, headers={"Retry-After": "0"})
    rsps.get(PAGE, body="ok")

    client.get(PAGE)
    assert backoff.sleeps == [2.0]


def test_very_long_retry_after_gives_up_without_waiting(
    rsps: responses.RequestsMock, client: PoliteClient, backoff: Backoff
) -> None:
    allow_robots(rsps)
    rsps.get(PAGE, status=503, headers={"Retry-After": "3600"})

    with pytest.raises(RetriesExhausted, match="wait 3600"):
        client.get(PAGE)
    assert backoff.sleeps == []


def test_parse_retry_after() -> None:
    now = datetime(2026, 10, 24, 12, 0, 0, tzinfo=UTC)
    assert parse_retry_after("120", now) == 120.0
    assert parse_retry_after("Sat, 24 Oct 2026 12:00:30 GMT", now) == 30.0
    assert parse_retry_after("Sat, 24 Oct 2026 11:00:00 GMT", now) == 0.0
    assert parse_retry_after("soon", now) is None
    assert parse_retry_after(None, now) is None


def test_client_errors_are_not_retried(rsps: responses.RequestsMock, client: PoliteClient) -> None:
    allow_robots(rsps)
    rsps.get(PAGE, status=404)

    with pytest.raises(HttpStatusError) as err:
        client.get(PAGE)

    assert err.value.status == 404
    assert len(page_calls(rsps)) == 1
    assert client.blocked is None


# Stop conditions


@pytest.mark.parametrize("status", [401, 403])
def test_auth_errors_block_the_source_for_the_day(
    rsps: responses.RequestsMock, client: PoliteClient, status: int
) -> None:
    allow_robots(rsps)
    rsps.get(PAGE, status=status)

    with capture_logs() as logs, pytest.raises(BlockedError) as err:
        client.get(PAGE)
    assert err.value.status == status
    assert any(log["event"] == "source_blocked" for log in logs)

    # Every later request fails without touching the network
    with pytest.raises(BlockedError, match="already blocked"):
        client.get(f"{BASE}/jobs/2")
    assert len(page_calls(rsps)) == 1


def test_captcha_page_blocks_the_source(rsps: responses.RequestsMock, client: PoliteClient) -> None:
    allow_robots(rsps)
    rsps.get(
        PAGE,
        body='<html><body><div class="g-recaptcha"></div></body></html>',
        content_type="text/html",
    )
    with pytest.raises(BlockedError, match="captcha"):
        client.get(PAGE)


def test_challenge_page_with_503_is_not_retried(
    rsps: responses.RequestsMock, client: PoliteClient, backoff: Backoff
) -> None:
    allow_robots(rsps)
    rsps.get(
        PAGE,
        status=503,
        body="<html><head><title>Just a moment...</title></head></html>",
        content_type="text/html",
    )
    with pytest.raises(BlockedError):
        client.get(PAGE)
    assert backoff.sleeps == []


def test_redirect_to_login_blocks_the_source(
    rsps: responses.RequestsMock, client: PoliteClient
) -> None:
    allow_robots(rsps)
    rsps.get(PAGE, status=302, headers={"Location": "/account/login?next=/jobs/1"})

    with pytest.raises(BlockedError, match="login page"):
        client.get(PAGE)
    assert page_calls(rsps) == [PAGE]


# Redirects


def test_redirects_are_followed_through_the_same_rules(
    rsps: responses.RequestsMock, client: PoliteClient
) -> None:
    allow_robots(rsps)
    allow_robots(rsps, "https://careers.example.org")
    rsps.get(PAGE, status=301, headers={"Location": "https://careers.example.org/jobs/1"})
    rsps.get("https://careers.example.org/jobs/1", body="moved")

    result = client.get(PAGE)

    assert result.url == "https://careers.example.org/jobs/1"
    assert result.text == "moved"


def test_redirect_into_a_disallowed_path_is_skipped(
    rsps: responses.RequestsMock, client: PoliteClient
) -> None:
    allow_robots(rsps, body="User-agent: *\nDisallow: /private/\n")
    rsps.get(PAGE, status=302, headers={"Location": "/private/1"})

    with pytest.raises(DisallowedByRobots):
        client.get(PAGE)


def test_redirect_loops_stop(rsps: responses.RequestsMock, client: PoliteClient) -> None:
    allow_robots(rsps)
    rsps.get(PAGE, status=302, headers={"Location": PAGE})
    with pytest.raises(TooManyRedirects):
        client.get(PAGE)


# Conditional requests


def test_conditional_request_headers_are_sent(
    rsps: responses.RequestsMock, client: PoliteClient
) -> None:
    allow_robots(rsps)
    rsps.get(PAGE, status=304, headers={"ETag": '"v1"'})

    result = client.get(PAGE, etag='"v1"', last_modified="Fri, 23 Oct 2026 10:00:00 GMT")

    sent = rsps.calls[-1].request.headers
    assert sent["If-None-Match"] == '"v1"'
    assert sent["If-Modified-Since"] == "Fri, 23 Oct 2026 10:00:00 GMT"
    assert result.not_modified
    assert result.content == b""


def test_validators_are_returned_for_the_next_run(
    rsps: responses.RequestsMock, client: PoliteClient
) -> None:
    allow_robots(rsps)
    rsps.get(
        PAGE,
        body="<html></html>",
        headers={"ETag": '"v2"', "Last-Modified": "Sat, 24 Oct 2026 06:00:00 GMT"},
    )

    result = client.get(PAGE)

    assert "If-None-Match" not in rsps.calls[-1].request.headers
    assert result.etag == '"v2"'
    assert result.last_modified == "Sat, 24 Oct 2026 06:00:00 GMT"
    assert not result.not_modified


# Stats


def test_stats_count_requests_and_statuses(
    rsps: responses.RequestsMock, client: PoliteClient
) -> None:
    allow_robots(rsps)
    rsps.get(PAGE, status=503)
    rsps.get(PAGE, body="12345")
    rsps.get(f"{BASE}/jobs/2", status=404)

    client.get(PAGE)
    with pytest.raises(HttpStatusError):
        client.get(f"{BASE}/jobs/2")

    assert client.stats.as_dict() == {
        "requests": 4,
        "retries": 1,
        "network_errors": 0,
        "bytes_received": 5,
        "robots_skipped": 0,
        "status_counts": {"200": 2, "404": 1, "503": 1},
    }
