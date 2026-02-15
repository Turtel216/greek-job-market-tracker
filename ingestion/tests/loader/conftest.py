"""Fixtures for the loader tests."""

from datetime import UTC, date, datetime

import pytest
from loader_helpers import DAY_1, JSON, Posting, WriteRun

from jobtracker_ingest.core.manifest import RunManifest, RunWriter
from jobtracker_ingest.core.models import ListingRef, RawPayload
from jobtracker_ingest.core.storage import MemoryObjectStore


@pytest.fixture
def store() -> MemoryObjectStore:
    return MemoryObjectStore()


@pytest.fixture
def write_run(store: MemoryObjectStore) -> WriteRun:
    """Write a complete run, as extract would, and return its manifest."""

    def write(
        postings: list[Posting],
        run_date: date = DAY_1,
        run_id: str = "20261025T030000Z-aaaa0001",
        manifest: bool = True,
    ) -> RunManifest:
        started = datetime(run_date.year, run_date.month, run_date.day, 3, tzinfo=UTC)
        writer = RunWriter(store, "greenhouse", run_date, run_id)
        for posting in postings:
            url = (
                f"https://job-boards.greenhouse.io/acropolisanalytics/jobs/{posting.source_job_id}"
            )
            ref = ListingRef(
                source_job_id=posting.source_job_id,
                url=url,
                listing_fingerprint=posting.listing_fingerprint,
                account="acropolisanalytics",
            )
            detail = None
            if posting.content is not None:
                detail = writer.save_detail(
                    RawPayload(
                        source="greenhouse",
                        source_job_id=posting.source_job_id,
                        url=url,
                        run_date=run_date,
                        fetched_at=started.replace(minute=5),
                        content=posting.content,
                        content_type=JSON,
                        account="acropolisanalytics",
                    )
                )
            writer.add_posting(ref, detail)
        result = RunManifest(
            source="greenhouse",
            run_date=run_date,
            run_id=run_id,
            started_at=started,
            finished_at=started.replace(minute=10),
            adapter_version="0.1.0",
            parser_version="1",
            counts={"postings": len(postings)},
            http={"requests": len(postings) + 1, "status_counts": {"200": len(postings) + 1}},
            listing_pages=[],
            postings=writer.postings,
            errors=[],
        )
        if manifest:
            writer.write_manifest(result)
        return result

    return write
