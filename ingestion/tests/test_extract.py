import gzip
import json
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
import responses

from jobtracker_ingest.core.http import BlockedError, PoliteClient
from jobtracker_ingest.core.models import RawPayload
from jobtracker_ingest.core.ratelimit import HostRateLimiter
from jobtracker_ingest.core.registry import SourceConfig
from jobtracker_ingest.core.storage import MemoryObjectStore
from jobtracker_ingest.extract import ExtractError, new_run_id, run_extract
from jobtracker_ingest.sources.greenhouse import GreenhouseAdapter

FIXTURES = Path(__file__).parent / "fixtures" / "greenhouse"
API = "https://boards-api.greenhouse.io/v1/boards"
BOARD = f"{API}/acropolisanalytics/jobs"
UA = "GreekTechJobsTracker/0.1.0 (+https://github.com/example/jobs; maintainer@example.org)"
RUN_DATE = date(2026, 10, 25)
PREFIX = "raw/greenhouse/2026-10-25/r1/"
JSON = "application/json; charset=utf-8"


@pytest.fixture
def rsps() -> Iterator[responses.RequestsMock]:
    with responses.RequestsMock() as mock:
        mock.get("https://boards-api.greenhouse.io/robots.txt", status=404)
        yield mock


@pytest.fixture
def store() -> MemoryObjectStore:
    return MemoryObjectStore()


def make_client() -> PoliteClient:
    limiter = HostRateLimiter(3, max_jitter_seconds=0, sleep=lambda s: None)
    return PoliteClient(UA, 3, limiter=limiter, sleep=lambda s: None)


def config(accounts: list[str] | None = None) -> SourceConfig:
    return SourceConfig(
        name="greenhouse",
        kind="ats_api",
        base_url=API,
        accounts=accounts or ["acropolisanalytics"],
    )


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def detail_url(job_id: str) -> str:
    return f"{BOARD}/{job_id}?pay_transparency=true"


def mock_board(rsps: responses.RequestsMock) -> None:
    rsps.get(BOARD, body=fixture("listing-board.json"), content_type=JSON)
    rsps.get(
        detail_url("4012345008"),
        body=fixture("detail-senior-data-engineer.json"),
        content_type=JSON,
    )
    rsps.get(detail_url("4012345009"), body=fixture("detail-backend-greek.json"), content_type=JSON)
    rsps.get(detail_url("4012345010"), body=fixture("detail-remote-flags.json"), content_type=JSON)


def manifest_json(store: MemoryObjectStore) -> dict[str, object]:
    data: dict[str, object] = json.loads(store.get(f"{PREFIX}_manifest.json"))
    return data


def test_new_run_ids_sort_by_time_and_are_unique() -> None:
    first, second = new_run_id(), new_run_id()
    assert first != second
    assert len(first) == len("20261025T030000Z-0123abcd")


def test_writes_payloads_then_the_manifest(
    rsps: responses.RequestsMock, store: MemoryObjectStore
) -> None:
    mock_board(rsps)

    manifest = run_extract(
        GreenhouseAdapter(), config(), RUN_DATE, store, make_client(), run_id="r1"
    )

    assert store.put_order == [
        f"{PREFIX}listing/page-0001.json.gz",
        f"{PREFIX}detail/4012345008.json.gz",
        f"{PREFIX}detail/4012345009.json.gz",
        f"{PREFIX}detail/4012345010.json.gz",
        f"{PREFIX}_manifest.json",
    ]
    listing = gzip.decompress(store.get(f"{PREFIX}listing/page-0001.json.gz"))
    assert listing == fixture("listing-board.json")
    assert manifest.counts == {"details_fetched": 3, "listing_pages": 1, "postings": 3}
    assert manifest.parser_version == GreenhouseAdapter.PARSER_VERSION
    assert manifest.http["requests"] == 5
    assert manifest_json(store) == manifest.model_dump(mode="json")


def test_manifest_is_enough_to_parse_again(
    rsps: responses.RequestsMock, store: MemoryObjectStore
) -> None:
    mock_board(rsps)
    manifest = run_extract(
        GreenhouseAdapter(), config(), RUN_DATE, store, make_client(), run_id="r1"
    )

    posting = manifest.postings[0]
    assert posting.detail is not None
    payload = RawPayload(
        source=manifest.source,
        source_job_id=posting.source_job_id,
        url=posting.detail.url,
        run_date=manifest.run_date,
        fetched_at=posting.detail.fetched_at,
        content=gzip.decompress(store.get(posting.detail.key)),
        content_type=posting.detail.content_type,
        account=posting.account,
        object_key=posting.detail.key,
    )
    parsed = GreenhouseAdapter().parse(payload)
    assert parsed.title_raw == "Senior Data Engineer"
    assert parsed.object_key == f"{PREFIX}detail/4012345008.json.gz"


def test_a_gone_posting_is_listed_without_a_detail(
    rsps: responses.RequestsMock, store: MemoryObjectStore
) -> None:
    rsps.get(BOARD, body=fixture("listing-board.json"), content_type=JSON)
    rsps.get(detail_url("4012345008"), status=404)
    rsps.get(detail_url("4012345009"), body=fixture("detail-backend-greek.json"), content_type=JSON)
    rsps.get(detail_url("4012345010"), body=fixture("detail-remote-flags.json"), content_type=JSON)

    manifest = run_extract(
        GreenhouseAdapter(), config(), RUN_DATE, store, make_client(), run_id="r1"
    )

    assert manifest.counts["detail_errors"] == 1
    assert manifest.postings[0].detail is None
    assert manifest.errors[0].url == detail_url("4012345008")


def test_details_can_be_skipped(rsps: responses.RequestsMock, store: MemoryObjectStore) -> None:
    rsps.get(BOARD, body=fixture("listing-board.json"), content_type=JSON)
    rsps.get(detail_url("4012345009"), body=fixture("detail-backend-greek.json"), content_type=JSON)

    manifest = run_extract(
        GreenhouseAdapter(),
        config(),
        RUN_DATE,
        store,
        make_client(),
        run_id="r1",
        needs_detail=lambda ref: ref.source_job_id == "4012345009",
    )

    assert manifest.counts["details_skipped"] == 2
    assert [p.detail is not None for p in manifest.postings] == [False, True, False]


def test_a_posting_on_two_boards_is_kept_once(
    rsps: responses.RequestsMock, store: MemoryObjectStore
) -> None:
    mock_board(rsps)
    rsps.get(f"{API}/sister/jobs", body=fixture("listing-board.json"), content_type=JSON)

    manifest = run_extract(
        GreenhouseAdapter(),
        config(["acropolisanalytics", "sister"]),
        RUN_DATE,
        store,
        make_client(),
        run_id="r1",
    )

    assert manifest.counts["postings"] == 3
    assert manifest.counts["duplicate_postings"] == 3
    assert manifest.counts["listing_pages"] == 2


def test_zero_listings_fails_without_a_manifest(
    rsps: responses.RequestsMock, store: MemoryObjectStore
) -> None:
    rsps.get(BOARD, body=fixture("listing-empty.json"), content_type=JSON)

    with pytest.raises(ExtractError, match="zero listings"):
        run_extract(GreenhouseAdapter(), config(), RUN_DATE, store, make_client(), run_id="r1")

    assert f"{PREFIX}_manifest.json" not in store.objects


def test_a_block_stops_the_run_without_a_manifest(
    rsps: responses.RequestsMock, store: MemoryObjectStore
) -> None:
    rsps.get(BOARD, body=fixture("listing-board.json"), content_type=JSON)
    rsps.get(detail_url("4012345008"), status=403)

    with pytest.raises(BlockedError):
        run_extract(GreenhouseAdapter(), config(), RUN_DATE, store, make_client(), run_id="r1")

    assert f"{PREFIX}_manifest.json" not in store.objects
