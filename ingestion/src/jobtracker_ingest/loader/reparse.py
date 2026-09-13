"""Load stored extract runs again after a parser fix, without scraping again.

A reparse loads the newest complete run of each day in a date range with
the current parser. Raw is never updated, so after a PARSER_VERSION bump
the new rows go in next to the old ones and staging reads the newest. With
the same parser version nothing new is inserted, so running a reparse twice
leaves the same end state.

A posting whose detail was not fetched on a day reuses the newest detail
fetched before that day, found in the manifests of the days before. That is
the detail the daily load used then, so the result does not depend on when
the reparse runs. ops.posting_fingerprints only knows the newest detail.

A day with no complete run, such as a failed extract or a day whose
payloads have expired, is skipped. A day that fails to load does not stop
the others.
"""

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, timedelta

import structlog
from structlog.contextvars import bound_contextvars

from jobtracker_ingest.core.manifest import DetailEntry
from jobtracker_ingest.core.storage import ObjectStore
from jobtracker_ingest.loader.load import Clock, LoadError, load_manifest, utc_now
from jobtracker_ingest.loader.models import IngestRun
from jobtracker_ingest.loader.runs import NoCompleteRun, latest_manifest
from jobtracker_ingest.loader.warehouse import Warehouse
from jobtracker_ingest.sources.base import SourceAdapter

# Payloads are kept for 30 days, so an older detail could not be read anyway
LOOKBACK_DAYS = 30


@dataclass
class ReparseResult:
    loaded: list[IngestRun] = field(default_factory=list)
    # Days with no complete run
    skipped: list[date] = field(default_factory=list)
    # Days whose load failed, with the error
    failed: dict[date, str] = field(default_factory=dict)

    @property
    def rows_inserted(self) -> int:
        return sum(run.rows_inserted for run in self.loaded)


def days(start: date, end: date) -> Iterator[date]:
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)


def run_reparse(
    adapter: SourceAdapter,
    source: str,
    start: date,
    end: date,
    store: ObjectStore,
    warehouse: Warehouse,
    now: Clock = utc_now,
) -> ReparseResult:
    if end < start:
        raise ValueError(f"end date {end} is before start date {start}")
    log = structlog.get_logger(__name__)
    result = ReparseResult()
    # The newest detail fetched so far for each posting
    details: dict[str, DetailEntry] = {}

    with bound_contextvars(source=source, parser_version=adapter.PARSER_VERSION):
        log.info("reparse_started", start=start.isoformat(), end=end.isoformat())
        for day in days(start - timedelta(days=LOOKBACK_DAYS), end):
            try:
                manifest = latest_manifest(store, source, day)
            except NoCompleteRun:
                if day >= start:
                    log.warning("reparse_day_skipped", run_date=day.isoformat())
                    result.skipped.append(day)
                continue

            if day >= start:
                try:
                    run = load_manifest(
                        adapter, manifest, store, warehouse, now=now, last_details=details
                    )
                    result.loaded.append(run)
                except LoadError as err:
                    # Already logged by the load
                    result.failed[day] = str(err)

            for posting in manifest.postings:
                if posting.detail is not None:
                    details[posting.source_job_id] = posting.detail

        log.info(
            "reparse_finished",
            loaded=[run.run_date.isoformat() for run in result.loaded],
            skipped=[day.isoformat() for day in result.skipped],
            failed=[day.isoformat() for day in result.failed],
            rows_inserted=result.rows_inserted,
        )
    return result
