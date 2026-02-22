"""Load one source's newest complete extract run for one run_date.

Follows the load step in the design doc:

1. Read the newest complete run for the source and date.
2. Parse each payload and scrub personal data.
3. Insert into raw.raw_postings, skipping rows that are already there.
4. Upsert ops.posting_fingerprints and write the run to ops.ingest_runs.
5. Fail if nothing was parsed from a non-empty listing.

A posting whose detail was not fetched this run reuses the last stored
detail from ops.posting_fingerprints, so it still gets a row for the day.
One posting that fails to parse is logged and skipped; the volume test in
dbt catches a parser that breaks on many of them.

Loading the same run again inserts nothing new and rewrites the same run
row, so a retried or repeated load leaves the same end state.
"""

import uuid
from collections import Counter
from collections.abc import Callable
from datetime import UTC, date, datetime
from uuid import UUID

import structlog
from structlog.contextvars import bound_contextvars

from jobtracker_ingest.core.manifest import PostingEntry, RunManifest
from jobtracker_ingest.core.models import RawPayload, RawPosting
from jobtracker_ingest.core.storage import ObjectNotFound, ObjectStore
from jobtracker_ingest.loader.models import Fingerprint, IngestRun
from jobtracker_ingest.loader.runs import fetched_payload, latest_manifest, reused_payload
from jobtracker_ingest.loader.warehouse import Warehouse
from jobtracker_ingest.privacy import scrub_posting
from jobtracker_ingest.sources.base import SourceAdapter

Clock = Callable[[], datetime]


class LoadError(Exception):
    """The run gave nothing to load, for example zero parsed rows."""


def utc_now() -> datetime:
    return datetime.now(UTC)


def run_load(
    adapter: SourceAdapter,
    source: str,
    run_date: date,
    store: ObjectStore,
    warehouse: Warehouse,
    load_id: UUID | None = None,
    now: Clock = utc_now,
) -> IngestRun:
    load_id = load_id or uuid.uuid4()
    started_at = now()
    with bound_contextvars(source=source, run_date=run_date.isoformat(), load_id=str(load_id)):
        manifest = latest_manifest(store, source, run_date)
        with bound_contextvars(run_id=manifest.run_id):
            return _load(adapter, manifest, store, warehouse, load_id, started_at, now)


def _load(
    adapter: SourceAdapter,
    manifest: RunManifest,
    store: ObjectStore,
    warehouse: Warehouse,
    load_id: UUID,
    started_at: datetime,
    now: Clock,
) -> IngestRun:
    log = structlog.get_logger(__name__)
    log.info("load_started", postings=len(manifest.postings))
    counts: Counter[str] = Counter()
    rows: list[RawPosting] = []
    fingerprints: list[Fingerprint] = []
    # Only read when some posting needs its last detail
    known: dict[str, Fingerprint] | None = None

    for posting in manifest.postings:
        reused = posting.detail is None
        try:
            if posting.detail is not None:
                payload = fetched_payload(store, manifest, posting, posting.detail)
                detail = (posting.detail.key, posting.detail.fetched_at)
            else:
                if known is None:
                    known = warehouse.fingerprints(manifest.source)
                last = known.get(posting.source_job_id)
                if last is None:
                    raise ObjectNotFound(f"no stored detail for {posting.source_job_id}")
                payload = reused_payload(store, manifest, posting, last)
                # Keeps its first fetch time, so extract still fetches it
                # again once it is 14 days old
                detail = (last.detail_object_key, last.detail_fetched_at)
        except ObjectNotFound as err:
            counts["details_missing"] += 1
            log.warning("detail_missing", source_job_id=posting.source_job_id, error=str(err))
            continue

        try:
            row = scrub_posting(adapter.parse(payload))
        except Exception as err:
            # A parser bug on one page must not stop the whole source. Only
            # the error goes in the log, never the page itself.
            counts["parse_errors"] += 1
            log.warning(
                "parse_failed",
                source_job_id=posting.source_job_id,
                object_key=payload.object_key,
                error=f"{type(err).__name__}: {err}",
            )
            continue

        if reused:
            counts["details_reused"] += 1
        rows.append(row)
        fingerprints.append(fingerprint_for(posting, payload, row, manifest.run_date, *detail))

    run = IngestRun(
        source=manifest.source,
        run_id=manifest.run_id,
        run_date=manifest.run_date,
        load_id=load_id,
        status="succeeded",
        adapter_version=manifest.adapter_version,
        parser_version=adapter.PARSER_VERSION,
        extract_started_at=manifest.started_at,
        extract_finished_at=manifest.finished_at,
        load_started_at=started_at,
        load_finished_at=started_at,
        postings_listed=len(manifest.postings),
        rows_parsed=len(rows),
        rows_inserted=0,
        details_reused=counts["details_reused"],
        details_missing=counts["details_missing"],
        parse_errors=counts["parse_errors"],
        extract_errors=len(manifest.errors),
        extract_counts=manifest.counts,
        http_stats=manifest.http,
    )

    if manifest.postings and not rows:
        error = f"zero rows parsed from {len(manifest.postings)} postings"
        failed = run.model_copy(
            update={"status": "failed", "error": error, "load_finished_at": now()}
        )
        with warehouse.transaction() as tx:
            tx.upsert_ingest_run(failed)
        log.error("load_failed", error=error, **dict(counts))
        raise LoadError(error)

    with warehouse.transaction() as tx:
        inserted = tx.insert_raw_postings(rows, load_id)
        tx.upsert_fingerprints(fingerprints)
        run = run.model_copy(update={"rows_inserted": inserted, "load_finished_at": now()})
        tx.upsert_ingest_run(run)

    log.info(
        "load_finished",
        rows_parsed=run.rows_parsed,
        rows_inserted=run.rows_inserted,
        details_reused=run.details_reused,
        details_missing=run.details_missing,
        parse_errors=run.parse_errors,
    )
    return run


def fingerprint_for(
    posting: PostingEntry,
    payload: RawPayload,
    row: RawPosting,
    run_date: date,
    detail_key: str,
    detail_fetched_at: datetime,
) -> Fingerprint:
    """The fingerprint row after this load."""
    return Fingerprint(
        source=row.source,
        source_job_id=row.source_job_id,
        listing_fingerprint=posting.listing_fingerprint,
        account=posting.account,
        url=posting.url,
        last_seen_run_date=run_date,
        detail_object_key=detail_key,
        detail_url=payload.url,
        detail_content_type=payload.content_type,
        detail_fetched_at=detail_fetched_at,
        content_hash=row.content_hash,
    )
