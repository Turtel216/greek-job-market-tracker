"""Run one source's extract for one run_date.

Fetches every listing page, then the detail of each posting that needs it,
and stores all payloads under a new run_id. The manifest is written last,
only when the run finished, so a failed run leaves no manifest behind.

A block or repeated network errors stop the run. A single posting or board
that answers with a 4xx, or that robots.txt disallows, is skipped and
listed in the manifest errors.
"""

import uuid
from collections import Counter
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, timedelta

import structlog
from structlog.contextvars import bound_contextvars

from jobtracker_ingest import __version__
from jobtracker_ingest.core.http import DisallowedByRobots, HttpStatusError, PoliteClient
from jobtracker_ingest.core.manifest import RunManifest, RunWriter
from jobtracker_ingest.core.models import ListingRef
from jobtracker_ingest.core.registry import SourceConfig
from jobtracker_ingest.core.storage import ObjectStore
from jobtracker_ingest.loader.models import Fingerprint
from jobtracker_ingest.sources.base import RunContext, SourceAdapter

# Decides whether a posting's detail is fetched this run
NeedsDetail = Callable[[ListingRef], bool]

# A stored detail older than this is fetched again even if the listing
# looks the same, in case the site changed the page without the listing
DETAIL_MAX_AGE = timedelta(days=14)


class ExtractError(Exception):
    """The run produced nothing usable, for example zero listings."""


def new_run_id(now: datetime | None = None) -> str:
    """Sortable by start time, so the newest run is the last one listed."""
    started = now or datetime.now(UTC)
    return f"{started:%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"


def fetch_everything(ref: ListingRef) -> bool:
    return True


def detail_needed(known: Mapping[str, Fingerprint], now: datetime) -> NeedsDetail:
    """The detail fetch strategy, from what the loader stored.

    Fetch the detail when the posting is new, its listing fingerprint
    changed, or the stored detail is older than 14 days. Otherwise the
    loader reuses the stored detail.
    """

    def needs(ref: ListingRef) -> bool:
        last = known.get(ref.source_job_id)
        return (
            last is None
            or last.listing_fingerprint != ref.listing_fingerprint
            or now - last.detail_fetched_at > DETAIL_MAX_AGE
        )

    return needs


def run_extract(
    adapter: SourceAdapter,
    config: SourceConfig,
    run_date: date,
    store: ObjectStore,
    client: PoliteClient,
    run_id: str | None = None,
    needs_detail: NeedsDetail = fetch_everything,
) -> RunManifest:
    started_at = datetime.now(UTC)
    run_id = run_id or new_run_id(started_at)
    # Bound for every log line in the run, including the HTTP client's
    with bound_contextvars(source=config.name, run_date=run_date.isoformat(), run_id=run_id):
        return _run(adapter, config, run_date, store, client, run_id, needs_detail, started_at)


def _run(
    adapter: SourceAdapter,
    config: SourceConfig,
    run_date: date,
    store: ObjectStore,
    client: PoliteClient,
    run_id: str,
    needs_detail: NeedsDetail,
    started_at: datetime,
) -> RunManifest:
    log = structlog.get_logger(__name__)
    writer = RunWriter(store, config.name, run_date, run_id)
    ctx = RunContext(config, run_date, run_id, client, writer, log)
    counts: Counter[str] = Counter()
    seen: set[str] = set()

    log.info("extract_started")
    for ref in adapter.list_postings(ctx):
        if ref.source_job_id in seen:
            counts["duplicate_postings"] += 1
            log.info("duplicate_posting", source_job_id=ref.source_job_id)
            continue
        seen.add(ref.source_job_id)

        detail = None
        if needs_detail(ref):
            try:
                detail = writer.save_detail(adapter.fetch_detail(ref, ctx))
                counts["details_fetched"] += 1
            except (HttpStatusError, DisallowedByRobots) as err:
                counts["detail_errors"] += 1
                log.warning("detail_skipped", url=err.url, error=str(err))
                writer.record_error(err.url, str(err))
        else:
            counts["details_skipped"] += 1
        writer.add_posting(ref, detail)

    if not writer.postings:
        raise ExtractError(
            f"zero listings for {config.name} on {run_date} "
            f"({len(writer.listing_pages)} pages, {len(writer.errors)} errors)"
        )

    counts["listing_pages"] = len(writer.listing_pages)
    counts["postings"] = len(writer.postings)
    manifest = RunManifest(
        source=config.name,
        run_date=run_date,
        run_id=run_id,
        started_at=started_at,
        finished_at=datetime.now(UTC),
        adapter_version=__version__,
        parser_version=adapter.PARSER_VERSION,
        counts=dict(sorted(counts.items())),
        http=client.stats.as_dict(),
        listing_pages=writer.listing_pages,
        postings=writer.postings,
        errors=writer.errors,
    )
    key = writer.write_manifest(manifest)
    log.info("extract_finished", manifest=key, **manifest.counts)
    return manifest
