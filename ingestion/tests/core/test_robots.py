import pytest

from jobtracker_ingest.core.robots import RobotsRules, robots_url

ROBOTS = """
User-agent: *
Disallow: /search
Disallow: /account/

User-agent: GreekTechJobsTracker
Disallow: /private/
Crawl-delay: 10
"""


def test_robots_url_keeps_scheme_and_host_only() -> None:
    url = "https://www.example.org:8443/jobs/123?page=2"
    assert robots_url(url) == "https://www.example.org:8443/robots.txt"


def test_our_own_section_is_used() -> None:
    rules = RobotsRules.from_response(200, ROBOTS)
    assert rules.can_fetch("https://example.org/jobs/123")
    assert not rules.can_fetch("https://example.org/private/data")
    # The * section does not apply once there is a section for us
    assert rules.can_fetch("https://example.org/search")


def test_wildcard_section_applies_when_we_have_no_section() -> None:
    rules = RobotsRules.from_response(200, "User-agent: *\nDisallow: /search\n")
    assert not rules.can_fetch("https://example.org/search?q=python")
    assert rules.can_fetch("https://example.org/jobs/123")
    assert rules.crawl_delay is None


def test_crawl_delay_is_read() -> None:
    assert RobotsRules.from_response(200, ROBOTS).crawl_delay == 10.0


def test_empty_file_allows_everything() -> None:
    assert RobotsRules.from_response(200, "").can_fetch("https://example.org/anything")


@pytest.mark.parametrize("status", [404, 410, 400])
def test_missing_file_allows_everything(status: int) -> None:
    rules = RobotsRules.from_response(status)
    assert rules.can_fetch("https://example.org/search")
    assert "allow all" in rules.reason


@pytest.mark.parametrize("status", [None, 500, 503, 401, 403])
def test_unreachable_file_disallows_everything(status: int | None) -> None:
    rules = RobotsRules.from_response(status)
    assert not rules.can_fetch("https://example.org/")
    assert "disallow all" in rules.reason
