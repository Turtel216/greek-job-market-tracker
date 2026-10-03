"""Rows the loader writes to the ops schema.

The columns match infra/postgres/init/03-raw-and-ops-tables.sql.
"""

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from jobtracker_ingest.core.models import Model


class Fingerprint(Model):
    """One row of ops.posting_fingerprints."""

    source: str
    source_job_id: str
    listing_fingerprint: str
    account: str | None
    url: str
    last_seen_run_date: date
    # The newest stored detail, reused on days it is not fetched
    detail_object_key: str
    detail_url: str
    detail_content_type: str
    detail_fetched_at: datetime
    content_hash: str


class IngestRun(Model):
    """One row of ops.ingest_runs, for one loaded extract run."""

    source: str
    run_id: str
    run_date: date
    load_id: UUID
    status: Literal["succeeded", "failed"]
    error: str | None = None
    adapter_version: str
    parser_version: str
    extract_started_at: datetime
    extract_finished_at: datetime
    load_started_at: datetime
    load_finished_at: datetime
    postings_listed: int
    rows_parsed: int
    rows_inserted: int
    details_reused: int
    details_missing: int
    parse_errors: int
    extract_errors: int
    extract_counts: dict[str, int]
    http_stats: dict[str, Any]


class DbtResult(Model):
    """One row of ops.dbt_runs, for one node in one dbt invocation."""

    invocation_id: UUID
    unique_id: str
    run_date: date
    # The dbt step, such as staging or marts, named after its artifacts folder
    step: str
    resource_type: str
    name: str
    status: str
    failures: int | None = None
    message: str | None = None
    rows_affected: int | None = None
    execution_time: float | None = None
    generated_at: datetime
