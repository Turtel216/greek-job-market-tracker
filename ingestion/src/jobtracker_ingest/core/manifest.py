"""Writing one extract run to the object store.

Every payload goes under raw/<source>/<run_date>/<run_id>/ and the manifest
is written last. A run without a manifest is incomplete and the loader
ignores it. The manifest also carries what the loader needs to rebuild each
RawPayload, so stored payloads can be parsed again without scraping.
"""

import json
from datetime import date, datetime
from typing import Any, Literal
from urllib.parse import quote

from jobtracker_ingest.core.http import FetchResult
from jobtracker_ingest.core.models import ListingRef, Model, RawPayload
from jobtracker_ingest.core.storage import MANIFEST_NAME, ObjectStore, gzip_bytes, run_prefix

MANIFEST_VERSION = 1


class ListingPageEntry(Model):
    key: str
    url: str
    account: str | None
    status: int
    content_type: str
    fetched_at: datetime


class DetailEntry(Model):
    key: str
    url: str
    content_type: str
    fetched_at: datetime


class PostingEntry(Model):
    source_job_id: str
    url: str
    listing_fingerprint: str
    account: str | None
    # None when the detail was not fetched this run, so the loader reuses
    # the last parsed detail
    detail: DetailEntry | None


class ErrorEntry(Model):
    url: str
    error: str


class RunManifest(Model):
    manifest_version: int = MANIFEST_VERSION
    source: str
    run_date: date
    run_id: str
    status: Literal["complete"] = "complete"
    started_at: datetime
    finished_at: datetime
    adapter_version: str
    parser_version: str
    counts: dict[str, int]
    http: dict[str, Any]
    listing_pages: list[ListingPageEntry]
    postings: list[PostingEntry]
    errors: list[ErrorEntry]


class ObjectExistsError(Exception):
    """Raw objects are never overwritten."""


def extension_for(content_type: str) -> str:
    return "json" if "json" in content_type.lower() else "html"


class RunWriter:
    """Stores the payloads of one run and keeps track of them for the manifest."""

    def __init__(self, store: ObjectStore, source: str, run_date: date, run_id: str) -> None:
        self.store = store
        self.source = source
        self.run_date = run_date
        self.run_id = run_id
        self.prefix = run_prefix(source, run_date, run_id)
        self.listing_pages: list[ListingPageEntry] = []
        self.postings: list[PostingEntry] = []
        self.errors: list[ErrorEntry] = []
        self._written: set[str] = set()

    def save_listing_page(self, result: FetchResult, account: str | None = None) -> str:
        number = len(self.listing_pages) + 1
        key = f"{self.prefix}listing/page-{number:04d}.{extension_for(result.content_type)}.gz"
        self._put(key, gzip_bytes(result.content), "application/gzip")
        self.listing_pages.append(
            ListingPageEntry(
                key=key,
                url=result.url,
                account=account,
                status=result.status,
                content_type=result.content_type,
                fetched_at=result.fetched_at,
            )
        )
        return key

    def save_detail(self, payload: RawPayload) -> RawPayload:
        """Store a detail payload and return it with its object key set."""
        # quote keeps odd characters in an id from changing the key layout
        name = quote(payload.source_job_id, safe="")
        key = f"{self.prefix}detail/{name}.{extension_for(payload.content_type)}.gz"
        self._put(key, gzip_bytes(payload.content), "application/gzip")
        return payload.model_copy(update={"object_key": key})

    def add_posting(self, ref: ListingRef, detail: RawPayload | None) -> None:
        entry = None
        if detail is not None and detail.object_key:
            entry = DetailEntry(
                key=detail.object_key,
                url=detail.url,
                content_type=detail.content_type,
                fetched_at=detail.fetched_at,
            )
        self.postings.append(
            PostingEntry(
                source_job_id=ref.source_job_id,
                url=ref.url,
                listing_fingerprint=ref.listing_fingerprint,
                account=ref.account,
                detail=entry,
            )
        )

    def record_error(self, url: str, error: str) -> None:
        self.errors.append(ErrorEntry(url=url, error=error))

    def write_manifest(self, manifest: RunManifest) -> str:
        key = f"{self.prefix}{MANIFEST_NAME}"
        body = json.dumps(manifest.model_dump(mode="json"), indent=2, ensure_ascii=False)
        self._put(key, body.encode("utf-8"), "application/json")
        return key

    def _put(self, key: str, body: bytes, content_type: str) -> None:
        if key in self._written:
            raise ObjectExistsError(f"{key} was already written in this run")
        self.store.put(key, body, content_type)
        self._written.add(key)
