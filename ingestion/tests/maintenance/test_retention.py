from datetime import UTC, date, datetime

import pytest

from jobtracker_ingest.core.storage import MemoryObjectStore
from jobtracker_ingest.loader.models import Fingerprint
from jobtracker_ingest.loader.warehouse import MemoryWarehouse
from jobtracker_ingest.maintenance.retention import cutoff_for, key_run_date, prune_raw

TODAY = date(2026, 11, 28)


def put(store: MemoryObjectStore, *keys: str) -> None:
    for key in keys:
        store.put(key, b"payload", "application/gzip")


def seen(warehouse: MemoryWarehouse, job_id: str, detail_key: str, last_seen: date) -> None:
    with warehouse.transaction() as tx:
        tx.upsert_fingerprints(
            [
                Fingerprint(
                    source="kariera",
                    source_job_id=job_id,
                    listing_fingerprint="fp",
                    account="it-jobs",
                    url=f"https://example.org/jobs/{job_id}",
                    last_seen_run_date=last_seen,
                    detail_object_key=detail_key,
                    detail_url=f"https://example.org/jobs/{job_id}",
                    detail_content_type="text/html",
                    detail_fetched_at=datetime(2026, 10, 1, 3, tzinfo=UTC),
                    content_hash="abc",
                )
            ]
        )


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("raw/kariera/2026-10-29/r1/_manifest.json", date(2026, 10, 29)),
        ("raw/kariera/2026-10-29/r1/detail/1.html.gz", date(2026, 10, 29)),
        ("raw/kariera/2026-10-29/_manifest.json", None),
        ("raw/kariera/20261029/r1/_manifest.json", None),
        ("raw/kariera/2026-02-30/r1/_manifest.json", None),
        ("other/kariera/2026-10-29/r1/_manifest.json", None),
    ],
)
def test_key_run_date(key: str, expected: date | None) -> None:
    assert key_run_date(key) == expected


def test_cutoff_keeps_30_days() -> None:
    # The first stored day, 2026-10-29, is 30 days old on 2026-11-28
    assert cutoff_for(TODAY) == date(2026, 10, 29)


def test_deletes_only_days_before_the_cutoff() -> None:
    store = MemoryObjectStore()
    warehouse = MemoryWarehouse()
    put(
        store,
        "raw/kariera/2026-10-28/r1/_manifest.json",
        "raw/kariera/2026-10-28/r1/listing/page-0001.html.gz",
        "raw/skywalker/2026-10-27/r1/_manifest.json",
        # An incomplete run, without a manifest, expires too
        "raw/skywalker/2026-10-27/r0/listing/page-0001.xml.gz",
        "raw/kariera/2026-10-29/r1/_manifest.json",
        "raw/kariera/2026-11-28/r1/_manifest.json",
    )

    result = prune_raw(store, warehouse, TODAY)

    assert list(store.list_keys("raw/")) == [
        "raw/kariera/2026-10-29/r1/_manifest.json",
        "raw/kariera/2026-11-28/r1/_manifest.json",
    ]
    assert result.cutoff == date(2026, 10, 29)
    assert result.deleted == 4
    assert result.deleted_days == {"kariera/2026-10-28": 2, "skywalker/2026-10-27": 2}
    assert (result.kept_in_use, result.unknown) == (0, 0)


def test_keeps_old_details_of_postings_seen_recently() -> None:
    store = MemoryObjectStore()
    warehouse = MemoryWarehouse()
    reused = "raw/kariera/2026-10-01/r1/detail/1.html.gz"
    gone = "raw/kariera/2026-10-01/r1/detail/2.html.gz"
    put(store, reused, gone, "raw/kariera/2026-10-01/r1/_manifest.json")
    # Posting 1 is still listed and reuses its detail. Posting 2 was last
    # seen before the cutoff, so it fetches its detail again if it returns.
    seen(warehouse, "1", reused, date(2026, 11, 27))
    seen(warehouse, "2", gone, date(2026, 10, 28))

    result = prune_raw(store, warehouse, TODAY)

    assert list(store.list_keys("raw/")) == [reused]
    assert (result.deleted, result.kept_in_use) == (2, 1)


def test_leaves_keys_outside_the_layout_alone() -> None:
    store = MemoryObjectStore()
    put(store, "raw/notes.txt", "raw/kariera/latest/r1/_manifest.json", "spike/2020-01-01/x")

    result = prune_raw(store, MemoryWarehouse(), TODAY)

    # Only keys under raw/ are listed
    assert result.unknown == 2
    assert result.deleted == 0
    assert len(store.objects) == 3


def test_dry_run_deletes_nothing() -> None:
    store = MemoryObjectStore()
    put(store, "raw/kariera/2026-10-01/r1/_manifest.json")

    result = prune_raw(store, MemoryWarehouse(), TODAY, dry_run=True)

    assert result.deleted == 1
    assert list(store.list_keys("raw/")) == ["raw/kariera/2026-10-01/r1/_manifest.json"]


def test_keep_days_can_change() -> None:
    store = MemoryObjectStore()
    put(store, "raw/kariera/2026-11-20/r1/_manifest.json")
    assert prune_raw(store, MemoryWarehouse(), TODAY, keep_days=7).deleted == 1
    assert store.objects == {}


def test_keep_days_must_be_positive() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        prune_raw(MemoryObjectStore(), MemoryWarehouse(), TODAY, keep_days=0)
