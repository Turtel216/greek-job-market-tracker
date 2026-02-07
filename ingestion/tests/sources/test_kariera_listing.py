import gzip
import json
import re
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
import responses
import structlog
from bs4 import BeautifulSoup
from structlog.testing import capture_logs

from jobtracker_ingest.core.http import PoliteClient
from jobtracker_ingest.core.manifest import RunWriter
from jobtracker_ingest.core.ratelimit import HostRateLimiter
from jobtracker_ingest.core.registry import SourceConfig
from jobtracker_ingest.core.storage import MemoryObjectStore
from jobtracker_ingest.extract import run_extract
from jobtracker_ingest.sources import get_adapter
from jobtracker_ingest.sources.base import RunContext
from jobtracker_ingest.sources.kariera import (
    KarieraAdapter,
    flight_text,
    job_records,
    listing_fingerprint,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "kariera"
JOBS = "https://www.kariera.gr/jobs"
IT = f"{JOBS}/it-jobs"
UA = "GreekTechJobsTracker/0.1.0 (+https://github.com/example/jobs; maintainer@example.org)"

# kariera.gr's robots.txt as of the onboarding review
ROBOTS = """User-Agent: meta-webindexer
Disallow: /

User-Agent: *
Allow: /
Disallow: /admin/
Disallow: /_next/data/
Disallow: /api/
Disallow: *.json
"""


@pytest.fixture
def rsps() -> Iterator[responses.RequestsMock]:
    with responses.RequestsMock() as mock:
        mock.get("https://www.kariera.gr/robots.txt", body=ROBOTS)
        yield mock


def make_ctx(accounts: list[str], max_pages: int = 50) -> RunContext:
    config = SourceConfig(
        name="kariera",
        kind="job_board_html",
        base_url=JOBS,
        accounts=accounts,
        min_delay_seconds=5,
        max_pages=max_pages,
    )
    limiter = HostRateLimiter(5, max_jitter_seconds=0, sleep=lambda s: None)
    client = PoliteClient(UA, 5, limiter=limiter, sleep=lambda s: None)
    writer = RunWriter(MemoryObjectStore(), "kariera", date(2026, 10, 25), "r1")
    return RunContext(config, date(2026, 10, 25), "r1", client, writer, structlog.get_logger())


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def page(rsps: responses.RequestsMock, number: int, name: str, base: str = IT) -> None:
    rsps.get(
        f"{base}?page={number}",
        body=fixture(name),
        content_type="text/html; charset=utf-8",
        match=[responses.matchers.query_param_matcher({"page": str(number)})],
    )


def records_in(name: str) -> dict[str, dict[str, object]]:
    return job_records(flight_text(BeautifulSoup(fixture(name), "html.parser")))


def test_registered() -> None:
    assert isinstance(get_adapter("kariera"), KarieraAdapter)


def test_list_postings_reads_pages_until_one_is_empty(rsps: responses.RequestsMock) -> None:
    page(rsps, 1, "listing-page-1.html")
    page(rsps, 2, "listing-page-2.html")
    page(rsps, 3, "listing-past-the-end.html")
    ctx = make_ctx(["it-jobs"])

    with capture_logs() as logs:
        refs = list(KarieraAdapter().list_postings(ctx))

    # 410104 is on both pages and the entry with no id in its URL is skipped
    assert [r.source_job_id for r in refs] == [
        "410101",
        "410102",
        "410103",
        "410104",
        "410105",
        "410109",
    ]
    first = refs[0]
    assert first.url == f"{IT}/410101"
    assert first.account == "it-jobs"
    assert first.detail_url is None
    assert len(ctx.listings.listing_pages) == 3  # type: ignore[attr-defined]
    skipped = [log for log in logs if log["event"] == "listing_item_skipped"]
    assert [log["item"]["url"] for log in skipped] == [IT]
    assert not [log for log in logs if log["event"] == "max_pages_reached"]


def test_fingerprint_comes_from_the_job_record(rsps: responses.RequestsMock) -> None:
    page(rsps, 1, "listing-page-1.html")
    page(rsps, 2, "listing-past-the-end.html")
    refs = list(KarieraAdapter().list_postings(make_ctx(["it-jobs"])))

    record = records_in("listing-page-1.html")["410101"]
    item = {"@type": "ListItem", "position": 1, "name": "x", "url": f"{IT}/410101"}
    assert refs[0].listing_fingerprint == listing_fingerprint(item, record)
    assert len({r.listing_fingerprint for r in refs}) == len(refs)


def test_fingerprint_ignores_counters_but_not_the_posting() -> None:
    record = records_in("listing-page-1.html")["410101"]
    item = {"url": f"{IT}/410101"}
    before = listing_fingerprint(item, record)

    recount = json.loads(json.dumps(record))
    recount["metadata"]["companyJobNumber"] = 99
    recount["company"]["logoUrl"] = "https://example.org/new-logo.png"
    assert listing_fingerprint(item, recount) == before

    assert listing_fingerprint(item, {**record, "remote": "FULLY_REMOTE"}) != before
    assert listing_fingerprint(item, {**record, "maxSalaryRange": 3000}) != before


def test_fingerprint_without_a_record_hashes_the_list_item() -> None:
    item = {"@type": "ListItem", "name": "QA Automation Engineer", "url": f"{IT}/410109"}
    fingerprint = listing_fingerprint(item, None)
    assert re.fullmatch(r"[0-9a-f]{64}", fingerprint)
    assert fingerprint != listing_fingerprint({**item, "name": "Changed"}, None)


def test_stops_when_a_page_has_nothing_new(rsps: responses.RequestsMock) -> None:
    # A site that ignores the page number keeps sending page 1
    page(rsps, 1, "listing-page-1.html")
    page(rsps, 2, "listing-page-1.html")
    ctx = make_ctx(["it-jobs"])

    refs = list(KarieraAdapter().list_postings(ctx))

    assert len(refs) == 4
    assert len(ctx.listings.listing_pages) == 2  # type: ignore[attr-defined]


def test_max_pages_is_a_hard_limit(rsps: responses.RequestsMock) -> None:
    page(rsps, 1, "listing-page-1.html")
    ctx = make_ctx(["it-jobs"], max_pages=1)

    with capture_logs() as logs:
        refs = list(KarieraAdapter().list_postings(ctx))

    assert len(refs) == 4
    assert {"event": "max_pages_reached", "category": "it-jobs", "max_pages": 1}.items() <= next(
        log for log in logs if log["event"] == "max_pages_reached"
    ).items()


def test_a_missing_category_is_skipped(rsps: responses.RequestsMock) -> None:
    rsps.get(f"{JOBS}/gone-jobs?page=1", status=404, body="<html>not found</html>")
    page(rsps, 1, "listing-page-1.html")
    page(rsps, 2, "listing-past-the-end.html")
    ctx = make_ctx(["gone-jobs", "it-jobs"])

    refs = list(KarieraAdapter().list_postings(ctx))

    assert len(refs) == 4
    errors = ctx.listings.errors  # type: ignore[attr-defined]
    url = f"{JOBS}/gone-jobs?page=1"
    assert [(e.url, e.error) for e in errors] == [(url, f"HTTP 404: {url}")]


def test_each_category_is_listed_on_its_own(rsps: responses.RequestsMock) -> None:
    # The same posting can show up in two categories; extract drops the
    # repeat, so the adapter does not need to
    telecom = f"{JOBS}/telecommunications-jobs"
    page(rsps, 1, "listing-page-1.html")
    page(rsps, 2, "listing-past-the-end.html")
    page(rsps, 1, "listing-page-1.html", base=telecom)
    page(rsps, 2, "listing-past-the-end.html", base=telecom)

    refs = list(KarieraAdapter().list_postings(make_ctx(["it-jobs", "telecommunications-jobs"])))

    assert [r.account for r in refs] == ["it-jobs"] * 4 + ["telecommunications-jobs"] * 4


def test_fetch_detail(rsps: responses.RequestsMock) -> None:
    page(rsps, 1, "listing-page-1.html")
    rsps.get(
        f"{IT}/410102",
        body=fixture("detail-monthly-salary.html"),
        content_type="text/html; charset=utf-8",
    )
    ctx = make_ctx(["it-jobs"])
    adapter = KarieraAdapter()
    refs = adapter.list_postings(ctx)
    next(refs)
    ref = next(refs)

    payload = adapter.fetch_detail(ref, ctx)

    assert payload.source == "kariera"
    assert payload.source_job_id == "410102"
    assert payload.url == f"{IT}/410102"
    assert payload.account == "it-jobs"
    assert payload.content_type == "text/html; charset=utf-8"
    assert adapter.parse(payload).salary_raw == "2000 - 2500 EUR per month"


def test_a_full_extract_stores_html_payloads(rsps: responses.RequestsMock) -> None:
    page(rsps, 1, "listing-page-1.html")
    page(rsps, 2, "listing-past-the-end.html")
    details = {
        "410101": "detail-onsite-no-salary.html",
        "410102": "detail-monthly-salary.html",
        "410103": "detail-yearly-salary.html",
        "410104": "detail-single-amount-hybrid.html",
    }
    for job_id, name in details.items():
        rsps.get(f"{IT}/{job_id}", body=fixture(name), content_type="text/html; charset=utf-8")
    ctx = make_ctx(["it-jobs"])
    store = MemoryObjectStore()

    manifest = run_extract(
        KarieraAdapter(), ctx.config, date(2026, 10, 25), store, ctx.client, run_id="r1"
    )

    prefix = "raw/kariera/2026-10-25/r1/"
    assert [p.key for p in manifest.listing_pages] == [
        f"{prefix}listing/page-0001.html.gz",
        f"{prefix}listing/page-0002.html.gz",
    ]
    assert manifest.counts["details_fetched"] == 4
    detail = manifest.postings[1].detail
    assert detail is not None
    assert detail.key == f"{prefix}detail/410102.html.gz"
    assert gzip.decompress(store.get(detail.key)) == fixture("detail-monthly-salary.html")
