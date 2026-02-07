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


# The order these sites list their rules in does not matter, the longest
# match does. This is kariera.gr's file as of the onboarding review.
ALLOW_FIRST = """
User-Agent: meta-webindexer
Disallow: /

User-Agent: *
Allow: /
Disallow: /admin/
Disallow: /_next/data/
Disallow: /api/
Disallow: *.json
"""


@pytest.mark.parametrize(
    ("path", "allowed"),
    [
        ("/jobs/it-jobs?page=2", True),
        ("/jobs/it-jobs/355562", True),
        ("/api/jobs", False),
        ("/_next/data/build/jobs.json", False),
        ("/sitemap.json", False),
        ("/admin/", False),
    ],
)
def test_a_longer_disallow_beats_an_earlier_allow(path: str, allowed: bool) -> None:
    rules = RobotsRules.from_response(200, ALLOW_FIRST)
    assert rules.can_fetch(f"https://www.kariera.gr{path}") is allowed


def test_a_longer_allow_beats_a_disallow() -> None:
    rules = RobotsRules.from_response(200, "User-agent: *\nDisallow: /jobs\nAllow: /jobs/it\n")
    assert rules.can_fetch("https://example.org/jobs/it-jobs")
    assert not rules.can_fetch("https://example.org/jobs/retail")


def test_allow_wins_a_tie() -> None:
    rules = RobotsRules.from_response(200, "User-agent: *\nDisallow: /page\nAllow: /page\n")
    assert rules.can_fetch("https://example.org/page")


def test_dollar_anchors_the_end() -> None:
    rules = RobotsRules.from_response(200, "User-agent: *\nDisallow: /*.php$\n")
    assert not rules.can_fetch("https://example.org/index.php")
    assert rules.can_fetch("https://example.org/index.php?x=1")


def test_query_strings_are_matched() -> None:
    rules = RobotsRules.from_response(200, "User-agent: *\nDisallow: /*?sort=\n")
    assert not rules.can_fetch("https://example.org/jobs?sort=new")
    assert rules.can_fetch("https://example.org/jobs?page=2")


def test_one_group_can_name_several_agents() -> None:
    body = "User-agent: OtherBot\nUser-agent: greektechjobstracker/2.0\nDisallow: /x\n"
    assert not RobotsRules.from_response(200, body).can_fetch("https://example.org/x")


def test_groups_for_the_same_agent_are_merged() -> None:
    body = (
        "User-agent: *\nDisallow: /a\n\n"
        "User-agent: Other\nDisallow: /\n\n"
        "User-agent: *\nDisallow: /b\n"
    )
    rules = RobotsRules.from_response(200, body)
    assert not rules.can_fetch("https://example.org/a")
    assert not rules.can_fetch("https://example.org/b")
    assert rules.can_fetch("https://example.org/c")


def test_comments_empty_rules_and_junk_are_ignored() -> None:
    body = (
        "Disallow: /before-any-agent\n"
        "User-agent: * # everyone\n"
        "Disallow:\n"
        "Disallow: /private # not for bots\n"
        "this line is junk\n"
        "Crawl-delay: soon\n"
        "Crawl-delay: 4\n"
    )
    rules = RobotsRules.from_response(200, body)
    assert rules.can_fetch("https://example.org/before-any-agent")
    assert not rules.can_fetch("https://example.org/private")
    # An unreadable delay is skipped and the next one is used
    assert rules.crawl_delay == 4.0


def test_negative_crawl_delay_is_ignored() -> None:
    assert RobotsRules.from_response(200, "User-agent: *\nCrawl-delay: -1\n").crawl_delay is None


def test_robots_txt_itself_is_always_allowed() -> None:
    rules = RobotsRules.from_response(200, "User-agent: *\nDisallow: /\n")
    assert rules.can_fetch("https://example.org/robots.txt")
    assert not rules.can_fetch("https://example.org")
