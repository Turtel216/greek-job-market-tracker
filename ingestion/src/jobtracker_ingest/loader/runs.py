"""Find the extract run to load and rebuild its payloads from the object store.

Only runs with a manifest are complete, and the newest one wins. Run ids
start with the time the run started, so they sort by age.
"""

import gzip
from datetime import date

from jobtracker_ingest.core.manifest import DetailEntry, PostingEntry, RunManifest
from jobtracker_ingest.core.models import RawPayload
from jobtracker_ingest.core.storage import MANIFEST_NAME, ObjectStore
from jobtracker_ingest.loader.models import Fingerprint


class NoCompleteRun(Exception):
    """No run for this source and date has a manifest, so there is nothing to load."""


def date_prefix(source: str, run_date: date) -> str:
    return f"raw/{source}/{run_date.isoformat()}/"


def complete_run_ids(store: ObjectStore, source: str, run_date: date) -> list[str]:
    """Run ids with a manifest, oldest first."""
    prefix = date_prefix(source, run_date)
    suffix = f"/{MANIFEST_NAME}"
    run_ids = []
    for key in store.list_keys(prefix):
        run_id = key[len(prefix) : -len(suffix)]
        if key.endswith(suffix) and run_id and "/" not in run_id:
            run_ids.append(run_id)
    return sorted(run_ids)


def latest_manifest(store: ObjectStore, source: str, run_date: date) -> RunManifest:
    run_ids = complete_run_ids(store, source, run_date)
    if not run_ids:
        raise NoCompleteRun(f"no complete run for {source} on {run_date}")
    key = f"{date_prefix(source, run_date)}{run_ids[-1]}/{MANIFEST_NAME}"
    manifest = RunManifest.model_validate_json(store.get(key))
    if manifest.source != source or manifest.run_date != run_date:
        raise ValueError(f"manifest {key} is for {manifest.source} on {manifest.run_date}")
    return manifest


def fetched_payload(
    store: ObjectStore, manifest: RunManifest, posting: PostingEntry, detail: DetailEntry
) -> RawPayload:
    """The detail fetched in this run."""
    return RawPayload(
        source=manifest.source,
        source_job_id=posting.source_job_id,
        url=detail.url,
        run_date=manifest.run_date,
        fetched_at=detail.fetched_at,
        content=gzip.decompress(store.get(detail.key)),
        content_type=detail.content_type,
        account=posting.account,
        object_key=detail.key,
    )


def reused_payload(
    store: ObjectStore, manifest: RunManifest, posting: PostingEntry, last: Fingerprint
) -> RawPayload:
    """The last stored detail, for a posting whose detail was not fetched this run.

    fetched_at is the start of this run, so scraped_at says when the posting
    was last seen on the listing, which keeps source freshness honest.
    """
    return RawPayload(
        source=manifest.source,
        source_job_id=posting.source_job_id,
        url=last.detail_url,
        run_date=manifest.run_date,
        fetched_at=manifest.started_at,
        content=gzip.decompress(store.get(last.detail_object_key)),
        content_type=last.detail_content_type,
        account=posting.account,
        object_key=last.detail_object_key,
    )
