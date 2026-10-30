import gzip
import json
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
import responses
import structlog
from structlog.testing import capture_logs

from jobtracker_ingest.core.http import BlockedError, PoliteClient
from jobtracker_ingest.core.manifest import RunWriter
from jobtracker_ingest.core.models import ListingRef
from jobtracker_ingest.core.ratelimit import HostRateLimiter
from jobtracker_ingest.core.registry import SourceConfig
from jobtracker_ingest.core.storage import MemoryObjectStore
from jobtracker_ingest.extract import run_extract
from jobtracker_ingest.sources import get_adapter
from jobtracker_ingest.sources.base import RunContext
from jobtracker_ingest.sources.skywalker import (
    FeedError,
    SkywalkerAdapter,
    feed_items,
    listing_fingerprint,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "skywalker"
FEED = "https://www.skywalker.gr/el/xml/aggelies-ergasias"
POSTINGS = "https://www.skywalker.gr/el/aggelia-ergasias"
IT = "01hdtm1ff0g1rdef228eftwthm"
ENGINEERS = "01hdtm1ff0g1rdef228eftwthj"
UA = "GreekTechJobsTracker/0.1.0 (+https://github.com/example/jobs; maintainer@example.org)"

# The parts of skywalker.gr's robots.txt that apply to us, as of the
# onboarding review
ROBOTS = """User-agent: *
Allow: /
Disallow: /manage/
Disallow: /*/apply
"""

# The ids on listing-page-1.xml and listing-page-2.xml, in feed order
PAGE_1 = [
    "01m7b2c3d4e5f6g7h8j9k0m1n2",
    "01m7c3d4e5f6g7h8j9k0m1n2p3",
    "01m7d4e5f6g7h8j9k0m1n2p3q4",
    "01m7e5f6g7h8j9k0m1n2p3q4r5",
]
PAGE_2_NEW = ["01m7g7h8j9k0m1n2p3q4r5s6t7", "01m7h8j9k0m1n2p3q4r5s6t7v8"]


@pytest.fixture
def rsps() -> Iterator[responses.RequestsMock]:
    with responses.RequestsMock() as mock:
        mock.get("https://www.skywalker.gr/robots.txt", body=ROBOTS)
        yield mock


def make_ctx(accounts: list[str], max_pages: int = 10) -> RunContext:
    config = SourceConfig(
        name="skywalker",
        kind="job_board_html",
        base_url=FEED,
        accounts=accounts,
        min_delay_seconds=5,
        max_pages=max_pages,
    )
    limiter = HostRateLimiter(5, max_jitter_seconds=0, sleep=lambda s: None)
    client = PoliteClient(UA, 5, limiter=limiter, sleep=lambda s: None)
    writer = RunWriter(MemoryObjectStore(), "skywalker", date(2026, 10, 29), "r1")
    return RunContext(config, date(2026, 10, 29), "r1", client, writer, structlog.get_logger())


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def page(
    rsps: responses.RequestsMock, number: int, name: str, category: str = IT, **kwargs: object
) -> None:
    rsps.get(
        FEED,
        body=fixture(name),
        content_type="text/xml; charset=UTF-8",
        match=[responses.matchers.query_param_matcher({"category": category, "page": str(number)})],
        **kwargs,
    )


def all_pages(rsps: responses.RequestsMock, category: str = IT) -> None:
    page(rsps, 1, "listing-page-1.xml", category)
    page(rsps, 2, "listing-page-2.xml", category)
    page(rsps, 3, "listing-past-the-end.xml", category)


def test_registered() -> None:
    assert isinstance(get_adapter("skywalker"), SkywalkerAdapter)


def test_list_postings_reads_pages_until_one_is_empty(rsps: responses.RequestsMock) -> None:
    all_pages(rsps)
    ctx = make_ctx([IT])

    with capture_logs() as logs:
        refs = list(SkywalkerAdapter().list_postings(ctx))

    # The last posting of page 1 is on page 2 as well, and the item whose
    # link has no posting id is skipped
    assert [r.source_job_id for r in refs] == PAGE_1 + PAGE_2_NEW
    first = refs[0]
    assert first.url == f"{POSTINGS}/{PAGE_1[0]}/backend-developer--python-"
    assert first.account == IT
    assert first.detail_url is None
    assert len(ctx.listings.listing_pages) == 3  # type: ignore[attr-defined]
    skipped = [log for log in logs if log["event"] == "listing_item_skipped"]
    assert [log["link"] for log in skipped] == ["https://www.skywalker.gr/el/aggelies-ergasias"]
    assert not [log for log in logs if log["event"] == "max_pages_reached"]


def test_fetch_detail_hands_out_the_feed_item_without_a_request(
    rsps: responses.RequestsMock,
) -> None:
    all_pages(rsps)
    ctx = make_ctx([IT])
    adapter = SkywalkerAdapter()
    refs = list(adapter.list_postings(ctx))
    calls = len(rsps.calls)

    payload = adapter.fetch_detail(refs[0], ctx)

    assert len(rsps.calls) == calls
    assert payload.source == "skywalker"
    assert payload.source_job_id == PAGE_1[0]
    assert payload.url == refs[0].url
    assert payload.account == IT
    assert payload.content_type == "application/xml; charset=utf-8"
    assert payload.content.startswith(b"<?xml version='1.0' encoding='utf-8'?>\n<item>")
    assert payload.content.rstrip().endswith(b"</item>")
    # The item parses the same as the detail fixture made from it, apart
    # from when and where it was stored
    expected = json.loads((FIXTURES / "expected" / "detail-hybrid-athens.json").read_text("utf-8"))
    parsed = adapter.parse(payload).model_dump(mode="json")
    for key in ("scraped_at", "object_key"):
        parsed.pop(key)
        expected.pop(key)
    assert parsed == expected


def test_fetch_detail_needs_the_posting_to_be_listed_first() -> None:
    ref = ListingRef(
        source_job_id=PAGE_1[0], url=f"{POSTINGS}/{PAGE_1[0]}", listing_fingerprint="x"
    )
    with pytest.raises(LookupError, match="was not listed in this run"):
        SkywalkerAdapter().fetch_detail(ref, make_ctx([IT]))


def test_fingerprint_ignores_the_logo_but_not_the_posting() -> None:
    item = feed_items(fixture("listing-page-1.xml"), FEED)[0]
    before = listing_fingerprint(item)

    logo = item.find("companyLogo")
    assert logo is not None
    logo.text = "https://www.skywalker.gr/storage/logos/new/logo_small.jpg"
    assert listing_fingerprint(item) == before

    work_type = item.find("workType")
    assert work_type is not None
    work_type.text = "remote"
    assert listing_fingerprint(item) != before


def test_every_posting_gets_its_own_fingerprint(rsps: responses.RequestsMock) -> None:
    all_pages(rsps)
    refs = list(SkywalkerAdapter().list_postings(make_ctx([IT])))
    assert len({r.listing_fingerprint for r in refs}) == len(refs)


def test_stops_when_a_page_has_nothing_new(rsps: responses.RequestsMock) -> None:
    # A site that ignores the page number keeps sending page 1
    page(rsps, 1, "listing-page-1.xml")
    page(rsps, 2, "listing-page-1.xml")
    ctx = make_ctx([IT])

    refs = list(SkywalkerAdapter().list_postings(ctx))

    assert len(refs) == 4
    assert len(ctx.listings.listing_pages) == 2  # type: ignore[attr-defined]


def test_max_pages_is_a_hard_limit(rsps: responses.RequestsMock) -> None:
    page(rsps, 1, "listing-page-1.xml")
    ctx = make_ctx([IT], max_pages=1)

    with capture_logs() as logs:
        refs = list(SkywalkerAdapter().list_postings(ctx))

    assert len(refs) == 4
    assert {"event": "max_pages_reached", "category": IT, "max_pages": 1}.items() <= next(
        log for log in logs if log["event"] == "max_pages_reached"
    ).items()


def test_a_missing_category_is_skipped(rsps: responses.RequestsMock) -> None:
    gone = "gone"
    rsps.get(
        FEED,
        status=404,
        body="<html>not found</html>",
        match=[responses.matchers.query_param_matcher({"category": gone, "page": "1"})],
    )
    all_pages(rsps)
    ctx = make_ctx([gone, IT])

    refs = list(SkywalkerAdapter().list_postings(ctx))

    assert len(refs) == 6
    url = f"{FEED}?category={gone}&page=1"
    errors = ctx.listings.errors  # type: ignore[attr-defined]
    assert [(e.url, e.error) for e in errors] == [(url, f"HTTP 404: {url}")]


def test_a_posting_in_two_categories_keeps_its_first_payload(
    rsps: responses.RequestsMock,
) -> None:
    # Extract drops the repeat, so the adapter does not need to
    all_pages(rsps)
    all_pages(rsps, ENGINEERS)
    ctx = make_ctx([IT, ENGINEERS])
    adapter = SkywalkerAdapter()

    refs = list(adapter.list_postings(ctx))

    assert [r.account for r in refs] == [IT] * 6 + [ENGINEERS] * 6
    assert adapter.fetch_detail(refs[6], ctx).account == IT


@pytest.mark.parametrize(
    ("body", "error"),
    [
        (b"<!DOCTYPE html><html><body><p>Server Error</body></html>", "is not XML"),
        (b"<!DOCTYPE html><html><title>Server Error</title></html>", "is not an RSS feed"),
    ],
)
def test_a_page_that_is_not_the_feed_fails_the_run(
    rsps: responses.RequestsMock, body: bytes, error: str
) -> None:
    rsps.get(FEED, body=body, content_type="text/xml; charset=UTF-8")
    with pytest.raises(FeedError, match=error):
        list(SkywalkerAdapter().list_postings(make_ctx([IT])))


def test_a_cloudflare_challenge_stops_the_source(rsps: responses.RequestsMock) -> None:
    rsps.get(
        FEED,
        status=403,
        body="<html><title>Just a moment...</title><script>window._cf_chl_opt={}</script>",
        content_type="text/html; charset=UTF-8",
        headers={"cf-mitigated": "challenge"},
    )
    with pytest.raises(BlockedError):
        list(SkywalkerAdapter().list_postings(make_ctx([IT])))


def test_a_full_extract_stores_feed_pages_and_items(rsps: responses.RequestsMock) -> None:
    all_pages(rsps)
    ctx = make_ctx([IT])
    store = MemoryObjectStore()
    adapter = SkywalkerAdapter()

    manifest = run_extract(adapter, ctx.config, date(2026, 10, 29), store, ctx.client, run_id="r1")

    prefix = "raw/skywalker/2026-10-29/r1/"
    assert [p.key for p in manifest.listing_pages] == [
        f"{prefix}listing/page-0001.xml.gz",
        f"{prefix}listing/page-0002.xml.gz",
        f"{prefix}listing/page-0003.xml.gz",
    ]
    assert gzip.decompress(store.get(manifest.listing_pages[0].key)) == fixture(
        "listing-page-1.xml"
    )
    assert manifest.counts["details_fetched"] == 6
    assert "duplicate_postings" not in manifest.counts
    detail = manifest.postings[1].detail
    assert detail is not None
    assert detail.key == f"{prefix}detail/{PAGE_1[1]}.xml.gz"
    # Robots plus three feed pages, and nothing for the details
    assert len(rsps.calls) == 4
