import gzip
import json
from datetime import UTC, date, datetime

import pytest
from requests.structures import CaseInsensitiveDict

from jobtracker_ingest.core.http import FetchResult
from jobtracker_ingest.core.manifest import (
    ObjectExistsError,
    RunManifest,
    RunWriter,
    extension_for,
)
from jobtracker_ingest.core.models import ListingRef, RawPayload
from jobtracker_ingest.core.storage import MemoryObjectStore

RUN_DATE = date(2026, 10, 25)
FETCHED = datetime(2026, 10, 25, 3, 0, tzinfo=UTC)
PREFIX = "raw/greenhouse/2026-10-25/r1/"


@pytest.fixture
def store() -> MemoryObjectStore:
    return MemoryObjectStore()


@pytest.fixture
def writer(store: MemoryObjectStore) -> RunWriter:
    return RunWriter(store, "greenhouse", RUN_DATE, "r1")


def fetch_result(url: str, body: bytes, content_type: str = "application/json") -> FetchResult:
    return FetchResult(
        url=url,
        status=200,
        content=body,
        headers=CaseInsensitiveDict({"Content-Type": content_type}),
        encoding="utf-8",
        fetched_at=FETCHED,
        elapsed_seconds=0.1,
        attempts=1,
    )


def payload(job_id: str, content_type: str = "application/json") -> RawPayload:
    return RawPayload(
        source="greenhouse",
        source_job_id=job_id,
        url=f"https://example.org/jobs/{job_id}",
        run_date=RUN_DATE,
        fetched_at=FETCHED,
        content=b'{"id": 1}',
        content_type=content_type,
        account="example",
    )


@pytest.mark.parametrize(
    ("content_type", "ext"),
    [("application/json; charset=utf-8", "json"), ("text/html", "html"), ("", "html")],
)
def test_extension_for(content_type: str, ext: str) -> None:
    assert extension_for(content_type) == ext


def test_listing_pages_are_numbered_and_gzipped(
    writer: RunWriter, store: MemoryObjectStore
) -> None:
    first = writer.save_listing_page(fetch_result("https://x/a", b"{}"), account="a")
    second = writer.save_listing_page(fetch_result("https://x/b", b"<html>", "text/html"))
    assert first == f"{PREFIX}listing/page-0001.json.gz"
    assert second == f"{PREFIX}listing/page-0002.html.gz"
    assert gzip.decompress(store.get(first)) == b"{}"
    assert [(p.url, p.account) for p in writer.listing_pages] == [
        ("https://x/a", "a"),
        ("https://x/b", None),
    ]


def test_save_detail_sets_the_object_key(writer: RunWriter, store: MemoryObjectStore) -> None:
    stored = writer.save_detail(payload("123"))
    assert stored.object_key == f"{PREFIX}detail/123.json.gz"
    assert gzip.decompress(store.get(stored.object_key)) == b'{"id": 1}'


def test_odd_ids_cannot_change_the_key_layout(writer: RunWriter) -> None:
    stored = writer.save_detail(payload("../a/b", "text/html"))
    assert stored.object_key == f"{PREFIX}detail/..%2Fa%2Fb.html.gz"


def test_objects_are_never_overwritten(writer: RunWriter) -> None:
    writer.save_detail(payload("123"))
    with pytest.raises(ObjectExistsError):
        writer.save_detail(payload("123"))


def test_postings_without_a_detail(writer: RunWriter) -> None:
    ref = ListingRef(source_job_id="1", url="https://x/1", listing_fingerprint="f", account="a")
    writer.add_posting(ref, None)
    writer.add_posting(ref.model_copy(update={"source_job_id": "2"}), payload("2"))
    assert writer.postings[0].detail is None
    # A payload that was never stored has no key, so there is no detail to point at
    assert writer.postings[1].detail is None


def test_manifest_is_plain_json(writer: RunWriter, store: MemoryObjectStore) -> None:
    writer.record_error("https://x/gone", "HTTP 404")
    manifest = RunManifest(
        source="greenhouse",
        run_date=RUN_DATE,
        run_id="r1",
        started_at=FETCHED,
        finished_at=FETCHED,
        adapter_version="0.1.0",
        parser_version="1",
        counts={"postings": 0},
        http={},
        listing_pages=writer.listing_pages,
        postings=writer.postings,
        errors=writer.errors,
    )
    key = writer.write_manifest(manifest)
    assert key == f"{PREFIX}_manifest.json"
    assert store.content_types[key] == "application/json"
    data = json.loads(store.get(key))
    assert data["status"] == "complete"
    assert data["errors"] == [{"url": "https://x/gone", "error": "HTTP 404"}]
    assert RunManifest.model_validate(data) == manifest
