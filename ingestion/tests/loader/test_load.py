import json
from datetime import UTC, date, datetime
from uuid import UUID

import pytest
from loader_helpers import (
    DAY_1,
    DAY_2,
    DETAILS,
    FIXTURES,
    BrokenGreenhouse,
    Posting,
    WriteRun,
    fixture_posting,
    with_description,
)
from structlog.testing import capture_logs

from jobtracker_ingest.core.storage import MemoryObjectStore
from jobtracker_ingest.loader.load import LoadError, run_load
from jobtracker_ingest.loader.models import IngestRun
from jobtracker_ingest.loader.runs import NoCompleteRun
from jobtracker_ingest.loader.warehouse import MemoryWarehouse
from jobtracker_ingest.sources.greenhouse import GreenhouseAdapter

LOAD_1 = UUID("00000000-0000-0000-0000-000000000001")
LOAD_2 = UUID("00000000-0000-0000-0000-000000000002")
NOW = datetime(2026, 10, 25, 3, 30, tzinfo=UTC)
ALL = ["4012345008", "4012345009", "4012345010"]
PARSER = GreenhouseAdapter.PARSER_VERSION


@pytest.fixture
def warehouse() -> MemoryWarehouse:
    return MemoryWarehouse()


def load(
    store: MemoryObjectStore,
    warehouse: MemoryWarehouse,
    run_date: date = DAY_1,
    load_id: UUID = LOAD_1,
) -> IngestRun:
    return run_load(
        GreenhouseAdapter(),
        "greenhouse",
        run_date,
        store,
        warehouse,
        load_id=load_id,
        now=lambda: NOW,
    )


def expected(job_id: str) -> dict[str, object]:
    path = FIXTURES / "expected" / DETAILS[job_id]
    return json.loads(path.read_text(encoding="utf-8"))


def test_loads_every_posting(
    store: MemoryObjectStore, warehouse: MemoryWarehouse, write_run: WriteRun
) -> None:
    manifest = write_run([fixture_posting(job_id) for job_id in ALL])

    run = load(store, warehouse)

    assert sorted(job_id for _, job_id, _, _ in warehouse.raw_postings) == ALL
    row = warehouse.raw_postings[("greenhouse", "4012345009", DAY_1, PARSER)]
    want = expected("4012345009")
    for column in ("title_raw", "company_raw", "description_raw", "salary_raw", "content_hash"):
        assert row[column] == want[column], column
    assert row["load_id"] == LOAD_1
    assert row["object_key"] == manifest.postings[1].detail.key  # type: ignore[union-attr]
    assert row["scraped_at"] == datetime(2026, 10, 25, 3, 5, tzinfo=UTC)

    assert run.status == "succeeded"
    assert (run.postings_listed, run.rows_parsed, run.rows_inserted) == (3, 3, 3)
    assert (run.details_reused, run.details_missing, run.parse_errors) == (0, 0, 0)
    assert run.run_id == manifest.run_id
    assert run.extract_started_at == manifest.started_at
    assert run.http_stats == manifest.http
    assert warehouse.ingest_runs == {("greenhouse", manifest.run_id): run}

    fp = warehouse.posting_fingerprints[("greenhouse", "4012345009")]
    assert fp.listing_fingerprint == "fp-1"
    assert fp.last_seen_run_date == DAY_1
    assert fp.detail_object_key == row["object_key"]
    assert fp.detail_fetched_at == row["scraped_at"]
    assert fp.content_hash == row["content_hash"]


def test_loading_twice_leaves_the_same_state(
    store: MemoryObjectStore, warehouse: MemoryWarehouse, write_run: WriteRun
) -> None:
    write_run([fixture_posting(job_id) for job_id in ALL])
    load(store, warehouse, load_id=LOAD_1)
    raw = dict(warehouse.raw_postings)
    fingerprints = dict(warehouse.posting_fingerprints)

    again = load(store, warehouse, load_id=LOAD_2)

    assert warehouse.raw_postings == raw
    assert warehouse.posting_fingerprints == fingerprints
    assert (again.rows_parsed, again.rows_inserted) == (3, 0)
    # One row per extract run, now from the second load
    assert list(warehouse.ingest_runs.values()) == [again]


def test_an_unchanged_posting_reuses_its_last_detail(
    store: MemoryObjectStore, warehouse: MemoryWarehouse, write_run: WriteRun
) -> None:
    write_run([fixture_posting("4012345008")])
    load(store, warehouse)
    first = warehouse.raw_postings[("greenhouse", "4012345008", DAY_1, PARSER)]

    # Next day the fingerprint is the same, so extract skipped the detail
    day_2 = write_run(
        [fixture_posting("4012345008", fetch=False)],
        run_date=DAY_2,
        run_id="20261026T030000Z-bbbb0001",
    )
    run = load(store, warehouse, run_date=DAY_2, load_id=LOAD_2)

    row = warehouse.raw_postings[("greenhouse", "4012345008", DAY_2, PARSER)]
    assert row["content_hash"] == first["content_hash"]
    assert row["object_key"] == first["object_key"]
    # Seen on today's listing, even though the detail is older
    assert row["scraped_at"] == day_2.started_at
    assert run.details_reused == 1

    fp = warehouse.posting_fingerprints[("greenhouse", "4012345008")]
    assert fp.last_seen_run_date == DAY_2
    # Still the first fetch, so extract fetches it again after 14 days
    assert fp.detail_fetched_at == first["scraped_at"]


def test_a_detail_that_cannot_be_found_is_skipped(
    store: MemoryObjectStore, warehouse: MemoryWarehouse, write_run: WriteRun
) -> None:
    write_run([fixture_posting("4012345008")])
    load(store, warehouse)
    # The stored detail expired, and another posting was never loaded
    first_key = warehouse.posting_fingerprints[("greenhouse", "4012345008")].detail_object_key
    del store.objects[first_key]
    write_run(
        [
            fixture_posting("4012345008", fetch=False),
            fixture_posting("4012345010", fetch=False),
            fixture_posting("4012345009"),
        ],
        run_date=DAY_2,
        run_id="20261026T030000Z-bbbb0001",
    )

    with capture_logs() as logs:
        run = load(store, warehouse, run_date=DAY_2, load_id=LOAD_2)

    assert (run.rows_parsed, run.details_missing, run.details_reused) == (1, 2, 0)
    assert [log["source_job_id"] for log in logs if log["event"] == "detail_missing"] == [
        "4012345008",
        "4012345010",
    ]


def test_one_bad_payload_does_not_stop_the_load(
    store: MemoryObjectStore, warehouse: MemoryWarehouse, write_run: WriteRun
) -> None:
    write_run([fixture_posting("4012345008"), Posting("999", b"<html>not json</html>")])

    with capture_logs() as logs:
        run = load(store, warehouse)

    assert (run.rows_parsed, run.parse_errors, run.status) == (1, 1, "succeeded")
    failed = next(log for log in logs if log["event"] == "parse_failed")
    assert failed["source_job_id"] == "999"
    assert failed["error"].startswith("JSONDecodeError")
    # The posting that failed gets no fingerprint, so extract fetches it again
    assert ("greenhouse", "999") not in warehouse.posting_fingerprints


def test_zero_parsed_rows_fails_and_records_the_run(
    store: MemoryObjectStore, warehouse: MemoryWarehouse, write_run: WriteRun
) -> None:
    manifest = write_run([Posting("998", b"{"), Posting("999", b"[]")])

    with pytest.raises(LoadError, match="zero rows parsed from 2 postings"):
        load(store, warehouse)

    assert warehouse.raw_postings == {}
    assert warehouse.posting_fingerprints == {}
    run = warehouse.ingest_runs[("greenhouse", manifest.run_id)]
    assert (run.status, run.error, run.parse_errors) == (
        "failed",
        "zero rows parsed from 2 postings",
        2,
    )


def test_a_newer_run_after_a_failed_load_is_loaded(
    store: MemoryObjectStore, warehouse: MemoryWarehouse, write_run: WriteRun
) -> None:
    write_run([Posting("999", b"{")], run_id="20261025T030000Z-aaaa0001")
    with pytest.raises(LoadError):
        load(store, warehouse)
    # A new extract run for the same day works, and is the one loaded
    write_run([fixture_posting("4012345008")], run_id="20261025T050000Z-bbbb0002")

    run = load(store, warehouse, load_id=LOAD_2)

    statuses = {run_id: r.status for (_, run_id), r in warehouse.ingest_runs.items()}
    assert statuses == {"20261025T030000Z-aaaa0001": "failed", run.run_id: "succeeded"}


def test_a_failed_load_does_not_replace_a_succeeded_one(
    store: MemoryObjectStore, warehouse: MemoryWarehouse, write_run: WriteRun
) -> None:
    manifest = write_run([fixture_posting(job_id) for job_id in ALL])
    good = load(store, warehouse)

    # The same run loaded again with a broken parser, for example by a reparse
    with pytest.raises(LoadError):
        run_load(BrokenGreenhouse(), "greenhouse", DAY_1, store, warehouse, now=lambda: NOW)

    # Its rows are still in raw, so the run still counts as succeeded
    assert warehouse.ingest_runs == {("greenhouse", manifest.run_id): good}
    assert len(warehouse.raw_postings) == 3


def test_personal_data_is_scrubbed_before_it_is_stored(
    store: MemoryObjectStore, warehouse: MemoryWarehouse, write_run: WriteRun
) -> None:
    write_run([with_description("4012345009", "Contact: Maria P. at maria@example.gr")])

    load(store, warehouse)

    row = warehouse.raw_postings[("greenhouse", "4012345009", DAY_1, PARSER)]
    description = row["description_raw"]
    assert description.endswith("Contact: [contact]")
    assert "maria" not in description.lower()


def test_an_older_date_does_not_overwrite_a_newer_fingerprint(
    store: MemoryObjectStore, warehouse: MemoryWarehouse, write_run: WriteRun
) -> None:
    write_run(
        [fixture_posting("4012345008", fingerprint="new")],
        run_date=DAY_2,
        run_id="20261026T030000Z-bbbb0001",
    )
    load(store, warehouse, run_date=DAY_2)
    # A late load or reparse of the day before
    write_run([fixture_posting("4012345008", fingerprint="old")])
    load(store, warehouse, load_id=LOAD_2)

    fp = warehouse.posting_fingerprints[("greenhouse", "4012345008")]
    assert (fp.listing_fingerprint, fp.last_seen_run_date) == ("new", DAY_2)
    assert len(warehouse.raw_postings) == 2


def test_no_complete_run_writes_nothing(
    store: MemoryObjectStore, warehouse: MemoryWarehouse, write_run: WriteRun
) -> None:
    write_run([fixture_posting("4012345008")], manifest=False)

    with pytest.raises(NoCompleteRun):
        load(store, warehouse)

    assert warehouse.ingest_runs == {}


def test_memory_transaction_rolls_back_on_error(warehouse: MemoryWarehouse) -> None:
    with pytest.raises(RuntimeError), warehouse.transaction() as tx:
        tx.upsert_ingest_run(failed_run())
        raise RuntimeError("database went away")
    assert warehouse.ingest_runs == {}


def failed_run() -> IngestRun:
    return IngestRun(
        source="greenhouse",
        run_id="r1",
        run_date=DAY_1,
        load_id=LOAD_1,
        status="failed",
        adapter_version="0.1.0",
        parser_version="1",
        extract_started_at=NOW,
        extract_finished_at=NOW,
        load_started_at=NOW,
        load_finished_at=NOW,
        postings_listed=0,
        rows_parsed=0,
        rows_inserted=0,
        details_reused=0,
        details_missing=0,
        parse_errors=0,
        extract_errors=0,
        extract_counts={},
        http_stats={},
    )
