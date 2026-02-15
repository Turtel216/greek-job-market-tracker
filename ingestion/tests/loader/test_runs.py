import pytest
from loader_helpers import DAY_1, DAY_2, WriteRun, fixture_posting

from jobtracker_ingest.core.storage import MemoryObjectStore
from jobtracker_ingest.loader.runs import NoCompleteRun, complete_run_ids, latest_manifest


def test_only_runs_with_a_manifest_count(store: MemoryObjectStore, write_run: WriteRun) -> None:
    write_run([fixture_posting("4012345008")], run_id="20261025T030000Z-aaaa0001")
    write_run([fixture_posting("4012345009")], run_id="20261025T040000Z-bbbb0002")
    # Started last, but failed before writing its manifest
    write_run([fixture_posting("4012345010")], run_id="20261025T050000Z-cccc0003", manifest=False)
    # A key deeper in the tree that only looks like a manifest
    store.put("raw/greenhouse/2026-10-25/x/detail/_manifest.json", b"{}", "application/json")

    assert complete_run_ids(store, "greenhouse", DAY_1) == [
        "20261025T030000Z-aaaa0001",
        "20261025T040000Z-bbbb0002",
    ]
    manifest = latest_manifest(store, "greenhouse", DAY_1)
    assert manifest.run_id == "20261025T040000Z-bbbb0002"
    assert [p.source_job_id for p in manifest.postings] == ["4012345009"]


def test_no_complete_run(store: MemoryObjectStore, write_run: WriteRun) -> None:
    write_run([fixture_posting("4012345008")], manifest=False)
    with pytest.raises(NoCompleteRun, match="no complete run for greenhouse on 2026-10-25"):
        latest_manifest(store, "greenhouse", DAY_1)


def test_other_dates_are_not_mixed_in(store: MemoryObjectStore, write_run: WriteRun) -> None:
    write_run([fixture_posting("4012345008")], run_date=DAY_2, run_id="20261026T030000Z-dddd")
    with pytest.raises(NoCompleteRun):
        latest_manifest(store, "greenhouse", DAY_1)


def test_a_manifest_for_another_date_is_rejected(
    store: MemoryObjectStore, write_run: WriteRun
) -> None:
    manifest = write_run([fixture_posting("4012345008")], run_date=DAY_2)
    # A manifest copied under the wrong date by hand
    store.put(
        "raw/greenhouse/2026-10-25/r9/_manifest.json",
        manifest.model_dump_json().encode(),
        "application/json",
    )
    with pytest.raises(ValueError, match="is for greenhouse on 2026-10-26"):
        latest_manifest(store, "greenhouse", DAY_1)
