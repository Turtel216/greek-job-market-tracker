"""PostgresWarehouse against a real database, connected as the loader role.

Skipped unless JOBTRACKER_TEST_ADMIN_DSN and JOBTRACKER_TEST_LOADER_DSN are
set. `make test-db` starts a throwaway Postgres with the init scripts from
infra/postgres/init and runs these tests against it.
"""

import os
from collections.abc import Iterator
from datetime import UTC, date, datetime
from typing import Literal
from uuid import UUID

import psycopg
import pytest
from loader_helpers import DAY_1, DAY_2, WriteRun, fixture_posting

from jobtracker_ingest.core.models import RawPosting
from jobtracker_ingest.core.storage import MemoryObjectStore
from jobtracker_ingest.loader.load import run_load
from jobtracker_ingest.loader.models import Fingerprint, IngestRun
from jobtracker_ingest.loader.warehouse import PostgresWarehouse
from jobtracker_ingest.sources.greenhouse import GreenhouseAdapter

ADMIN_DSN = os.environ.get("JOBTRACKER_TEST_ADMIN_DSN")
LOADER_DSN = os.environ.get("JOBTRACKER_TEST_LOADER_DSN")

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not (ADMIN_DSN and LOADER_DSN), reason="no test database, run make test-db"),
]

LOAD_1 = UUID("00000000-0000-0000-0000-000000000001")
NOW = datetime(2026, 10, 25, 3, 30, tzinfo=UTC)


@pytest.fixture
def admin() -> Iterator[psycopg.Connection[tuple[object, ...]]]:
    with psycopg.connect(str(ADMIN_DSN), autocommit=True) as conn:
        conn.execute("truncate raw.raw_postings, ops.ingest_runs, ops.posting_fingerprints")
        yield conn


@pytest.fixture
def warehouse(
    admin: psycopg.Connection[tuple[object, ...]],
) -> Iterator[PostgresWarehouse]:
    with PostgresWarehouse.connect(LOADER_DSN) as warehouse:
        yield warehouse


def posting(job_id: str, title: str = "Data Engineer", parser_version: str = "1") -> RawPosting:
    return RawPosting(
        source="greenhouse",
        source_job_id=job_id,
        run_date=DAY_1,
        scraped_at=NOW,
        url=f"https://example.org/jobs/{job_id}",
        title_raw=title,
        company_raw="Acropolis Analytics",
        location_raw="Αθήνα",
        workplace_raw=None,
        employment_type_raw=None,
        salary_raw=None,
        department_raw=None,
        description_raw="Python και SQL",
        posted_at_raw=None,
        object_key=f"raw/greenhouse/2026-10-25/r1/detail/{job_id}.json.gz",
        parser_version=parser_version,
    )


def fingerprint(job_id: str, value: str, run_date: date = DAY_1) -> Fingerprint:
    return Fingerprint(
        source="greenhouse",
        source_job_id=job_id,
        listing_fingerprint=value,
        account="acropolisanalytics",
        url=f"https://example.org/jobs/{job_id}",
        last_seen_run_date=run_date,
        detail_object_key=f"raw/greenhouse/2026-10-25/r1/detail/{job_id}.json.gz",
        detail_url=f"https://example.org/api/jobs/{job_id}",
        detail_content_type="application/json",
        detail_fetched_at=NOW,
        content_hash="abc",
    )


def ingest_run(status: Literal["succeeded", "failed"] = "succeeded", rows: int = 3) -> IngestRun:
    return IngestRun(
        source="greenhouse",
        run_id="r1",
        run_date=DAY_1,
        load_id=LOAD_1,
        status=status,
        error=None if status == "succeeded" else "boom",
        adapter_version="0.1.0",
        parser_version="1",
        extract_started_at=NOW,
        extract_finished_at=NOW,
        load_started_at=NOW,
        load_finished_at=NOW,
        postings_listed=rows,
        rows_parsed=rows,
        rows_inserted=rows,
        details_reused=0,
        details_missing=0,
        parse_errors=0,
        extract_errors=0,
        extract_counts={"postings": rows},
        http_stats={"requests": 4, "status_counts": {"200": 4}},
    )


def test_raw_inserts_skip_rows_that_are_already_there(
    warehouse: PostgresWarehouse, admin: psycopg.Connection[tuple[object, ...]]
) -> None:
    with warehouse.transaction() as tx:
        assert tx.insert_raw_postings([posting("1"), posting("2")], LOAD_1) == 2
    with warehouse.transaction() as tx:
        changed = posting("2", title="Changed")
        assert tx.insert_raw_postings([changed, posting("3")], LOAD_1) == 1
        assert tx.insert_raw_postings([], LOAD_1) == 0

    rows = admin.execute(
        "select source_job_id, title_raw, location_raw, load_id, content_hash"
        " from raw.raw_postings order by source_job_id"
    ).fetchall()
    assert [(r[0], r[1], r[2], r[3]) for r in rows] == [
        ("1", "Data Engineer", "Αθήνα", LOAD_1),
        ("2", "Data Engineer", "Αθήνα", LOAD_1),
        ("3", "Data Engineer", "Αθήνα", LOAD_1),
    ]
    assert rows[0][4] == posting("1").content_hash


def test_a_new_parser_version_adds_rows_next_to_the_old_ones(
    warehouse: PostgresWarehouse, admin: psycopg.Connection[tuple[object, ...]]
) -> None:
    with warehouse.transaction() as tx:
        assert tx.insert_raw_postings([posting("1")], LOAD_1) == 1
    with warehouse.transaction() as tx:
        # A reparse of the same day after a parser fix
        fixed = posting("1", title="Data Engineer II", parser_version="2")
        assert tx.insert_raw_postings([fixed, posting("1", parser_version="2")], LOAD_1) == 1

    rows = admin.execute(
        "select parser_version, title_raw from raw.raw_postings order by raw_posting_id"
    ).fetchall()
    assert rows == [("1", "Data Engineer"), ("2", "Data Engineer II")]


@pytest.mark.parametrize(
    "statement",
    [
        "select count(*) from raw.raw_postings",
        "update raw.raw_postings set url = 'x'",
        "delete from raw.raw_postings",
    ],
)
def test_the_loader_role_can_only_insert_into_raw(
    warehouse: PostgresWarehouse, statement: str
) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        warehouse.conn.execute(statement)


def test_fingerprints_round_trip_and_keep_the_newest(warehouse: PostgresWarehouse) -> None:
    with warehouse.transaction() as tx:
        tx.upsert_fingerprints([fingerprint("1", "a", DAY_2), fingerprint("2", "b")])
        tx.upsert_fingerprints([])
    with warehouse.transaction() as tx:
        # An older date loses, a newer or equal one wins
        tx.upsert_fingerprints([fingerprint("1", "old", DAY_1), fingerprint("2", "new", DAY_2)])

    stored = warehouse.fingerprints("greenhouse")
    assert stored["1"] == fingerprint("1", "a", DAY_2)
    assert stored["2"] == fingerprint("2", "new", DAY_2)
    assert warehouse.fingerprints("kariera") == {}


def test_ingest_run_is_replaced_on_a_second_load(
    warehouse: PostgresWarehouse, admin: psycopg.Connection[tuple[object, ...]]
) -> None:
    with warehouse.transaction() as tx:
        tx.upsert_ingest_run(ingest_run(status="failed", rows=0))
    with warehouse.transaction() as tx:
        tx.upsert_ingest_run(ingest_run())

    rows = admin.execute(
        "select status, error, rows_parsed, extract_counts, http_stats from ops.ingest_runs"
    ).fetchall()
    assert rows == [
        ("succeeded", None, 3, {"postings": 3}, {"requests": 4, "status_counts": {"200": 4}})
    ]


def test_a_failed_load_does_not_replace_a_succeeded_one(
    warehouse: PostgresWarehouse, admin: psycopg.Connection[tuple[object, ...]]
) -> None:
    with warehouse.transaction() as tx:
        tx.upsert_ingest_run(ingest_run())
    with warehouse.transaction() as tx:
        tx.upsert_ingest_run(ingest_run(status="failed", rows=0))

    rows = admin.execute("select status, error, rows_parsed from ops.ingest_runs").fetchall()
    assert rows == [("succeeded", None, 3)]


def test_a_failed_transaction_writes_nothing(
    warehouse: PostgresWarehouse, admin: psycopg.Connection[tuple[object, ...]]
) -> None:
    with pytest.raises(RuntimeError), warehouse.transaction() as tx:
        tx.insert_raw_postings([posting("1")], LOAD_1)
        tx.upsert_ingest_run(ingest_run())
        raise RuntimeError("stop")

    assert admin.execute("select count(*) from raw.raw_postings").fetchone() == (0,)
    assert admin.execute("select count(*) from ops.ingest_runs").fetchone() == (0,)


def test_a_full_load(
    warehouse: PostgresWarehouse,
    admin: psycopg.Connection[tuple[object, ...]],
    store: MemoryObjectStore,
    write_run: WriteRun,
) -> None:
    write_run([fixture_posting(job_id) for job_id in ("4012345008", "4012345009")])
    write_run(
        [fixture_posting("4012345008", fetch=False), fixture_posting("4012345010")],
        run_date=DAY_2,
        run_id="20261026T030000Z-bbbb0001",
    )

    for day in (DAY_1, DAY_2, DAY_2):
        run_load(GreenhouseAdapter(), "greenhouse", day, store, warehouse, now=lambda: NOW)

    rows = admin.execute(
        "select run_date, source_job_id from raw.raw_postings order by 1, 2"
    ).fetchall()
    assert rows == [
        (DAY_1, "4012345008"),
        (DAY_1, "4012345009"),
        (DAY_2, "4012345008"),
        (DAY_2, "4012345010"),
    ]
    runs = admin.execute(
        "select run_date, rows_parsed, rows_inserted, details_reused"
        " from ops.ingest_runs order by run_date"
    ).fetchall()
    # The second load of day 2 found nothing new
    assert runs == [(DAY_1, 2, 2, 0), (DAY_2, 2, 0, 1)]
