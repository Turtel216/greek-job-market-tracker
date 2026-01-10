import json
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
import responses
import structlog

from jobtracker_ingest.core.http import PoliteClient
from jobtracker_ingest.core.manifest import RunWriter
from jobtracker_ingest.core.ratelimit import HostRateLimiter
from jobtracker_ingest.core.registry import SourceConfig
from jobtracker_ingest.core.storage import MemoryObjectStore
from jobtracker_ingest.sources import get_adapter
from jobtracker_ingest.sources.base import RunContext
from jobtracker_ingest.sources.greenhouse import (
    GreenhouseAdapter,
    html_to_text,
    listing_fingerprint,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "greenhouse"
API = "https://boards-api.greenhouse.io/v1/boards"
UA = "GreekTechJobsTracker/0.1.0 (+https://github.com/example/jobs; maintainer@example.org)"


@pytest.fixture
def rsps() -> Iterator[responses.RequestsMock]:
    with responses.RequestsMock() as mock:
        mock.get("https://boards-api.greenhouse.io/robots.txt", status=404)
        yield mock


def make_ctx(accounts: list[str]) -> RunContext:
    config = SourceConfig(name="greenhouse", kind="ats_api", base_url=API, accounts=accounts)
    limiter = HostRateLimiter(3, max_jitter_seconds=0, sleep=lambda s: None)
    client = PoliteClient(UA, 3, limiter=limiter, sleep=lambda s: None)
    writer = RunWriter(MemoryObjectStore(), "greenhouse", date(2026, 10, 25), "r1")
    return RunContext(config, date(2026, 10, 25), "r1", client, writer, structlog.get_logger())


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def test_registered() -> None:
    assert isinstance(get_adapter("greenhouse"), GreenhouseAdapter)
    with pytest.raises(KeyError):
        get_adapter("nope")


def test_list_postings_reads_every_board(rsps: responses.RequestsMock) -> None:
    rsps.get(f"{API}/acropolisanalytics/jobs", body=fixture("listing-board.json"))
    rsps.get(f"{API}/emptyco/jobs", body=fixture("listing-empty.json"))
    ctx = make_ctx(["acropolisanalytics", "emptyco"])

    refs = list(GreenhouseAdapter().list_postings(ctx))

    assert [r.source_job_id for r in refs] == ["4012345008", "4012345009", "4012345010"]
    first = refs[0]
    assert first.url == "https://job-boards.greenhouse.io/acropolisanalytics/jobs/4012345008"
    assert first.listing_fingerprint == "2026-10-20T11:42:07-04:00"
    assert first.account == "acropolisanalytics"
    assert first.detail_url == f"{API}/acropolisanalytics/jobs/4012345008?pay_transparency=true"
    assert len(ctx.listings.listing_pages) == 2  # type: ignore[attr-defined]


def test_a_missing_board_is_skipped(rsps: responses.RequestsMock) -> None:
    rsps.get(f"{API}/gone/jobs", status=404, json={"status": 404, "error": "Job not found"})
    rsps.get(f"{API}/acropolisanalytics/jobs", body=fixture("listing-board.json"))
    ctx = make_ctx(["gone", "acropolisanalytics"])

    refs = list(GreenhouseAdapter().list_postings(ctx))

    assert len(refs) == 3
    errors = ctx.listings.errors  # type: ignore[attr-defined]
    assert [(e.url, e.error) for e in errors] == [
        (f"{API}/gone/jobs", f"HTTP 404: {API}/gone/jobs")
    ]


def test_fetch_detail(rsps: responses.RequestsMock) -> None:
    rsps.get(f"{API}/acropolisanalytics/jobs", body=fixture("listing-board.json"))
    rsps.get(
        f"{API}/acropolisanalytics/jobs/4012345008?pay_transparency=true",
        body=fixture("detail-senior-data-engineer.json"),
        content_type="application/json; charset=utf-8",
    )
    ctx = make_ctx(["acropolisanalytics"])
    adapter = GreenhouseAdapter()
    ref = next(iter(adapter.list_postings(ctx)))

    payload = adapter.fetch_detail(ref, ctx)

    assert payload.source == "greenhouse"
    assert payload.source_job_id == "4012345008"
    assert payload.url == ref.url
    assert payload.account == "acropolisanalytics"
    assert payload.content_type == "application/json; charset=utf-8"
    assert adapter.parse(payload).title_raw == "Senior Data Engineer"


def test_fingerprint_falls_back_to_a_hash_of_the_card() -> None:
    job = json.loads(fixture("listing-board.json"))["jobs"][2]
    assert "updated_at" not in job
    fingerprint = listing_fingerprint(job)
    assert len(fingerprint) == 64
    assert fingerprint != listing_fingerprint({**job, "title": "Changed"})


def test_html_to_text_without_content() -> None:
    assert html_to_text(None) is None
