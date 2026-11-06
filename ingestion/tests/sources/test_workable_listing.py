import json
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
import responses
import structlog
from structlog.testing import capture_logs

from jobtracker_ingest.core.http import PoliteClient
from jobtracker_ingest.core.manifest import RunWriter
from jobtracker_ingest.core.ratelimit import HostRateLimiter
from jobtracker_ingest.core.registry import SourceConfig
from jobtracker_ingest.core.storage import MemoryObjectStore
from jobtracker_ingest.sources import get_adapter
from jobtracker_ingest.sources.base import RunContext
from jobtracker_ingest.sources.workable import (
    WorkableAdapter,
    listing_fingerprint,
    location_text,
    workplace_text,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "workable"
API = "https://www.workable.com/api/accounts"
UA = "GreekTechJobsTracker/0.1.0 (+https://github.com/example/jobs; maintainer@example.org)"
IDS = ["AAA1111111", "BBB2222222", "CCC3333333", "DDD4444444", "EEE5555555"]


@pytest.fixture
def rsps() -> Iterator[responses.RequestsMock]:
    with responses.RequestsMock() as mock:
        mock.get("https://www.workable.com/robots.txt", status=404)
        yield mock


def make_ctx(accounts: list[str]) -> RunContext:
    config = SourceConfig(
        name="workable",
        kind="ats_api",
        base_url=API,
        accounts=accounts,
        min_delay_seconds=5,
    )
    limiter = HostRateLimiter(5, max_jitter_seconds=0, sleep=lambda s: None)
    client = PoliteClient(UA, 5, limiter=limiter, sleep=lambda s: None)
    writer = RunWriter(MemoryObjectStore(), "workable", date(2026, 10, 25), "r1")
    return RunContext(config, date(2026, 10, 25), "r1", client, writer, structlog.get_logger())


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def test_registered() -> None:
    assert isinstance(get_adapter("workable"), WorkableAdapter)


def test_list_postings_reads_every_account_once(rsps: responses.RequestsMock) -> None:
    rsps.get(f"{API}/acropolis?details=true", body=fixture("listing-account.json"))
    rsps.get(f"{API}/emptyco?details=true", body=fixture("listing-empty.json"))
    ctx = make_ctx(["acropolis", "emptyco"])

    refs = list(WorkableAdapter().list_postings(ctx))

    assert [r.source_job_id for r in refs] == IDS
    assert refs[0].url == "https://apply.workable.com/j/AAA1111111"
    assert refs[0].account == "acropolis"
    assert refs[0].detail_url is None
    assert len(ctx.listings.listing_pages) == 2  # type: ignore[attr-defined]
    # robots.txt plus one request per account, nothing per posting
    assert len(rsps.calls) == 3


def test_fetch_detail_hands_out_the_job_without_a_request(rsps: responses.RequestsMock) -> None:
    rsps.get(f"{API}/acropolis?details=true", body=fixture("listing-account.json"))
    ctx = make_ctx(["acropolis"])
    adapter = WorkableAdapter()
    refs = list(adapter.list_postings(ctx))
    calls = len(rsps.calls)

    payload = adapter.fetch_detail(refs[1], ctx)

    assert len(rsps.calls) == calls
    assert payload.source_job_id == "BBB2222222"
    assert payload.account == "acropolis"
    # Parsing the payload gives the company name from the listing
    assert adapter.parse(payload).company_raw == "Acropolis Analytics"
    with pytest.raises(LookupError):
        adapter.fetch_detail(refs[1].model_copy(update={"source_job_id": "nope"}), ctx)


def test_missing_account_is_skipped(rsps: responses.RequestsMock) -> None:
    rsps.get(f"{API}/gone?details=true", status=404, body='{"error": "not-found"}')
    rsps.get(f"{API}/acropolis?details=true", body=fixture("listing-account.json"))
    ctx = make_ctx(["gone", "acropolis"])

    with capture_logs() as logs:
        refs = list(WorkableAdapter().list_postings(ctx))

    assert [r.source_job_id for r in refs] == IDS
    assert [log["account"] for log in logs if log["event"] == "account_skipped"] == ["gone"]
    assert len(ctx.listings.errors) == 1  # type: ignore[attr-defined]


def test_job_without_shortcode_or_url_is_skipped(rsps: responses.RequestsMock) -> None:
    body = b'{"name": "X", "jobs": [{"title": "No code", "url": "https://x"}, {"shortcode": "A"}]}'
    rsps.get(f"{API}/acropolis?details=true", body=body)

    refs = list(WorkableAdapter().list_postings(make_ctx(["acropolis"])))

    assert refs == []


def test_repeated_job_keeps_every_place(rsps: responses.RequestsMock) -> None:
    rsps.get(f"{API}/acropolis?details=true", body=fixture("listing-repeats.json"))
    ctx = make_ctx(["acropolis"])
    adapter = WorkableAdapter()

    refs = list(adapter.list_postings(ctx))

    assert [r.source_job_id for r in refs] == ["FFF6666666", "GGG7777777"]
    posting = adapter.parse(adapter.fetch_detail(refs[0], ctx))
    assert posting.location_raw == "London, England, United Kingdom; Athens, Attica, Greece; Poland"


def test_fingerprint_changes_when_a_place_is_added(rsps: responses.RequestsMock) -> None:
    rsps.get(f"{API}/acropolis?details=true", body=fixture("listing-repeats.json"))
    refs = list(WorkableAdapter().list_postings(make_ctx(["acropolis"])))
    job = json.loads(fixture("listing-repeats.json"))["jobs"][0]

    assert refs[0].listing_fingerprint != listing_fingerprint(job)


def test_same_job_in_two_accounts_keeps_the_first(rsps: responses.RequestsMock) -> None:
    rsps.get(f"{API}/acropolis?details=true", body=fixture("listing-repeats.json"))
    rsps.get(f"{API}/other?details=true", body=fixture("listing-repeats.json"))
    ctx = make_ctx(["acropolis", "other"])
    adapter = WorkableAdapter()

    refs = list(adapter.list_postings(ctx))

    assert len(refs) == 4
    assert adapter.fetch_detail(refs[2], ctx).account == "acropolis"


def test_fingerprint_changes_with_the_job() -> None:
    job = {"shortcode": "A", "title": "Dev"}
    assert listing_fingerprint(job) == listing_fingerprint({"title": "Dev", "shortcode": "A"})
    assert listing_fingerprint(job) != listing_fingerprint({**job, "title": "Senior Dev"})


def test_workplace_prefers_the_type_over_the_flag() -> None:
    assert workplace_text({"workplace_type": "hybrid", "telecommuting": True}) == "hybrid"
    assert workplace_text({"workplace_type": "on_site"}) == "on site"
    assert workplace_text({"telecommuting": True}) == "remote"
    assert workplace_text({"telecommuting": False}) is None


def test_location_skips_empty_and_repeated_places() -> None:
    assert location_text({"country": "Greece"}) == "Greece"
    assert location_text({"country": "Greece", "other_places": [{"country": "Greece"}]}) == "Greece"
    assert location_text({"other_places": [{"city": ""}]}) is None
