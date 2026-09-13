from datetime import UTC, date, datetime

import pytest
from loader_helpers import (
    DAY_1,
    DAY_2,
    OldGreenhouse,
    Posting,
    WriteRun,
    fixture_posting,
    with_description,
)

from jobtracker_ingest.core.storage import MemoryObjectStore
from jobtracker_ingest.loader.load import run_load
from jobtracker_ingest.loader.reparse import LOOKBACK_DAYS, ReparseResult, run_reparse
from jobtracker_ingest.loader.warehouse import MemoryWarehouse
from jobtracker_ingest.sources.greenhouse import GreenhouseAdapter

DAY_3 = date(2026, 10, 27)
NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
FIXED = GreenhouseAdapter.PARSER_VERSION


@pytest.fixture
def warehouse() -> MemoryWarehouse:
    return MemoryWarehouse()


def run_id(day: date) -> str:
    return f"{day:%Y%m%d}T030000Z-aaaa0001"


def daily_loads(store: MemoryObjectStore, warehouse: MemoryWarehouse, *days: date) -> None:
    """The daily loads, made with the parser before the fix."""
    for day in days:
        run_load(OldGreenhouse(), "greenhouse", day, store, warehouse, now=lambda: NOW)


def reparse(
    store: MemoryObjectStore, warehouse: MemoryWarehouse, start: date, end: date
) -> ReparseResult:
    adapter = GreenhouseAdapter()
    return run_reparse(adapter, "greenhouse", start, end, store, warehouse, now=lambda: NOW)


def titles(warehouse: MemoryWarehouse, version: str) -> dict[tuple[str, date], str | None]:
    return {
        (job_id, day): row["title_raw"]
        for (_, job_id, day, parser), row in warehouse.raw_postings.items()
        if parser == version
    }


def test_a_reparse_adds_rows_from_the_fixed_parser(
    store: MemoryObjectStore, warehouse: MemoryWarehouse, write_run: WriteRun
) -> None:
    write_run([fixture_posting("4012345008"), fixture_posting("4012345009")])
    write_run(
        [fixture_posting("4012345008", fetch=False), fixture_posting("4012345010")],
        run_date=DAY_2,
        run_id=run_id(DAY_2),
    )
    daily_loads(store, warehouse, DAY_1, DAY_2)
    old = titles(warehouse, "old")

    result = reparse(store, warehouse, DAY_1, DAY_2)

    # The old rows stay as they were, and each one gets a fixed row next to it
    assert titles(warehouse, "old") == old
    assert set(old.values()) == {None}
    fixed = titles(warehouse, FIXED)
    assert fixed.keys() == old.keys()
    assert None not in fixed.values()
    assert [run.run_date for run in result.loaded] == [DAY_1, DAY_2]
    assert (result.skipped, result.failed, result.rows_inserted) == ([], {}, 4)
    # The runs now name the parser that loaded them last
    assert {run.parser_version for run in warehouse.ingest_runs.values()} == {FIXED}


def test_reused_details_come_from_the_days_before(
    store: MemoryObjectStore, warehouse: MemoryWarehouse, write_run: WriteRun
) -> None:
    day_1 = write_run([fixture_posting("4012345008")])
    write_run([fixture_posting("4012345008", fetch=False)], run_date=DAY_2, run_id=run_id(DAY_2))
    # Fetched again on day 3, so the fingerprint now points at this detail
    write_run([with_description("4012345008", "Updated")], run_date=DAY_3, run_id=run_id(DAY_3))
    daily_loads(store, warehouse, DAY_1, DAY_2, DAY_3)

    # Day 1 is before the range, but its detail is the one day 2 reused
    result = reparse(store, warehouse, DAY_2, DAY_2)

    row = warehouse.raw_postings[("greenhouse", "4012345008", DAY_2, FIXED)]
    assert row["object_key"] == day_1.postings[0].detail.key  # type: ignore[union-attr]
    assert "Updated" not in row["description_raw"]
    assert result.loaded[0].details_reused == 1


def test_a_detail_older_than_the_lookback_is_not_searched_for(
    store: MemoryObjectStore, warehouse: MemoryWarehouse, write_run: WriteRun
) -> None:
    early = date.fromordinal(DAY_2.toordinal() - LOOKBACK_DAYS - 1)
    write_run([fixture_posting("4012345008")], run_date=early, run_id=run_id(early))
    write_run(
        [fixture_posting("4012345008", fetch=False), fixture_posting("4012345009")],
        run_date=DAY_2,
        run_id=run_id(DAY_2),
    )

    result = reparse(store, warehouse, DAY_2, DAY_2)

    [run] = result.loaded
    assert (run.rows_parsed, run.details_missing) == (1, 1)


def test_reparsing_twice_leaves_the_same_state(
    store: MemoryObjectStore, warehouse: MemoryWarehouse, write_run: WriteRun
) -> None:
    write_run([fixture_posting("4012345008"), fixture_posting("4012345009")])
    daily_loads(store, warehouse, DAY_1)
    reparse(store, warehouse, DAY_1, DAY_1)
    raw = dict(warehouse.raw_postings)
    fingerprints = dict(warehouse.posting_fingerprints)

    again = reparse(store, warehouse, DAY_1, DAY_1)

    assert warehouse.raw_postings == raw
    assert warehouse.posting_fingerprints == fingerprints
    assert again.rows_inserted == 0


def test_days_without_a_complete_run_are_skipped(
    store: MemoryObjectStore, warehouse: MemoryWarehouse, write_run: WriteRun
) -> None:
    write_run([fixture_posting("4012345008")])
    # Day 2's extract failed before it wrote a manifest
    write_run([fixture_posting("4012345009")], run_date=DAY_2, run_id=run_id(DAY_2), manifest=False)
    write_run([fixture_posting("4012345010")], run_date=DAY_3, run_id=run_id(DAY_3))

    result = reparse(store, warehouse, DAY_1, DAY_3)

    assert [run.run_date for run in result.loaded] == [DAY_1, DAY_3]
    assert result.skipped == [DAY_2]
    assert {day for _, _, day, _ in warehouse.raw_postings} == {DAY_1, DAY_3}


def test_a_day_that_fails_does_not_stop_the_others(
    store: MemoryObjectStore, warehouse: MemoryWarehouse, write_run: WriteRun
) -> None:
    write_run([Posting("999", b"{")])
    write_run([fixture_posting("4012345008")], run_date=DAY_2, run_id=run_id(DAY_2))

    result = reparse(store, warehouse, DAY_1, DAY_2)

    assert result.failed == {DAY_1: "zero rows parsed from 1 postings"}
    assert [run.run_date for run in result.loaded] == [DAY_2]


def test_an_end_before_the_start_is_refused(
    store: MemoryObjectStore, warehouse: MemoryWarehouse
) -> None:
    with pytest.raises(ValueError, match="before start date"):
        reparse(store, warehouse, DAY_2, DAY_1)
