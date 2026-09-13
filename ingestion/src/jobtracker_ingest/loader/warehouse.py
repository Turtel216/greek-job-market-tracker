"""Writing loads to the warehouse.

The loader connects as the loader role, which can only insert into raw and
read and write ops. Everything one load writes goes in one transaction, so
a failed load leaves nothing behind and a retry starts clean.

MemoryWarehouse does the same in memory for tests, with the same conflict
rules as the SQL below.
"""

import json
import os
from collections.abc import Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from datetime import date
from typing import Any, Protocol, Self
from uuid import UUID

import psycopg
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from jobtracker_ingest.core.models import RawPosting
from jobtracker_ingest.loader.models import Fingerprint, IngestRun

RAW_COLUMNS = (
    "source",
    "source_job_id",
    "run_date",
    "scraped_at",
    "url",
    "title_raw",
    "company_raw",
    "location_raw",
    "workplace_raw",
    "employment_type_raw",
    "salary_raw",
    "department_raw",
    "description_raw",
    "posted_at_raw",
    "content_hash",
    "object_key",
    "parser_version",
    "load_id",
)
FINGERPRINT_COLUMNS = tuple(Fingerprint.model_fields)
# The natural key of raw.raw_postings: source, source_job_id, run_date and
# parser_version
RawKey = tuple[str, str, date, str]
RUN_COLUMNS = tuple(IngestRun.model_fields)


def placeholders(columns: Sequence[str]) -> str:
    return ", ".join(f"%({c})s" for c in columns)


# The loader role may only insert into raw, and naming the conflict columns
# would need select on them, so this leaves the target out. The natural key
# is the only constraint a new row can break, since the id is generated.
INSERT_RAW = f"""
insert into raw.raw_postings ({", ".join(RAW_COLUMNS)})
values ({placeholders(RAW_COLUMNS)})
on conflict do nothing
"""

# A load for an older run_date, for example a reparse, never overwrites a
# newer fingerprint
UPSERT_FINGERPRINT = f"""
insert into ops.posting_fingerprints ({", ".join(FINGERPRINT_COLUMNS)})
values ({placeholders(FINGERPRINT_COLUMNS)})
on conflict (source, source_job_id) do update set
    {", ".join(f"{c} = excluded.{c}" for c in FINGERPRINT_COLUMNS[2:])},
    updated_at = now()
where excluded.last_seen_run_date >= posting_fingerprints.last_seen_run_date
"""

# A failed load never replaces a succeeded one, for example a reparse with a
# broken parser. The rows of the succeeded load are still in raw, so the run
# still counts.
UPSERT_RUN = f"""
insert into ops.ingest_runs ({", ".join(RUN_COLUMNS)})
values ({placeholders(RUN_COLUMNS)})
on conflict (source, run_id) do update set
    {", ".join(f"{c} = excluded.{c}" for c in RUN_COLUMNS[2:])}
where ingest_runs.status = 'failed' or excluded.status = 'succeeded'
"""

SELECT_FINGERPRINTS = f"""
select {", ".join(FINGERPRINT_COLUMNS)}
from ops.posting_fingerprints
where source = %(source)s
"""


class WarehouseTx(Protocol):
    def insert_raw_postings(self, postings: Sequence[RawPosting], load_id: UUID) -> int:
        """Insert new rows and return how many were new."""
        ...

    def upsert_fingerprints(self, fingerprints: Sequence[Fingerprint]) -> None: ...

    def upsert_ingest_run(self, run: IngestRun) -> None: ...


class Warehouse(Protocol):
    def fingerprints(self, source: str) -> dict[str, Fingerprint]:
        """The stored fingerprints of one source, by source_job_id."""
        ...

    def transaction(self) -> AbstractContextManager[WarehouseTx]:
        """Writes that are committed together, or not at all on an error."""
        ...


def raw_row(posting: RawPosting, load_id: UUID) -> dict[str, Any]:
    row = posting.model_dump(include=set(RAW_COLUMNS))
    row["load_id"] = load_id
    return row


def run_row(run: IngestRun) -> dict[str, Any]:
    row = run.model_dump()
    row["extract_counts"] = Jsonb(row["extract_counts"])
    row["http_stats"] = Jsonb(row["http_stats"])
    return row


def conninfo_from_env() -> str:
    """Connection settings for the loader role from the environment."""
    return make_conninfo(
        host=os.environ["POSTGRES_HOST"],
        port=os.environ.get("POSTGRES_PORT", "5432"),
        dbname=os.environ["WAREHOUSE_DB"],
        user=os.environ.get("LOADER_USER", "loader"),
        password=os.environ["LOADER_PASSWORD"],
        application_name="jobtracker-loader",
        connect_timeout=10,
    )


class PostgresTx:
    def __init__(self, conn: psycopg.Connection[Any]) -> None:
        self.conn = conn

    def insert_raw_postings(self, postings: Sequence[RawPosting], load_id: UUID) -> int:
        if not postings:
            return 0
        with self.conn.cursor() as cur:
            cur.executemany(INSERT_RAW, [raw_row(p, load_id) for p in postings])
            # executemany adds up the rows each statement inserted
            return cur.rowcount

    def upsert_fingerprints(self, fingerprints: Sequence[Fingerprint]) -> None:
        if not fingerprints:
            return
        with self.conn.cursor() as cur:
            cur.executemany(UPSERT_FINGERPRINT, [f.model_dump() for f in fingerprints])

    def upsert_ingest_run(self, run: IngestRun) -> None:
        self.conn.execute(UPSERT_RUN, run_row(run))


class PostgresWarehouse:
    def __init__(self, conn: psycopg.Connection[Any]) -> None:
        self.conn = conn

    @classmethod
    def connect(cls, conninfo: str | None = None) -> Self:
        # autocommit, so that only transaction() opens a transaction
        conn = psycopg.connect(conninfo or conninfo_from_env(), autocommit=True)
        return cls(conn)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def fingerprints(self, source: str) -> dict[str, Fingerprint]:
        with self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute(SELECT_FINGERPRINTS, {"source": source})
            rows = [Fingerprint.model_validate(row) for row in cur]
        return {row.source_job_id: row for row in rows}

    @contextmanager
    def transaction(self) -> Iterator[WarehouseTx]:
        with self.conn.transaction():
            yield PostgresTx(self.conn)


class MemoryWarehouse:
    """Keeps the three tables in dicts. Changes only stick if the transaction ends cleanly."""

    def __init__(self) -> None:
        self.raw_postings: dict[RawKey, dict[str, Any]] = {}
        self.posting_fingerprints: dict[tuple[str, str], Fingerprint] = {}
        self.ingest_runs: dict[tuple[str, str], IngestRun] = {}

    def fingerprints(self, source: str) -> dict[str, Fingerprint]:
        return {
            job_id: fp for (src, job_id), fp in self.posting_fingerprints.items() if src == source
        }

    @contextmanager
    def transaction(self) -> Iterator[WarehouseTx]:
        tx = MemoryTx(
            dict(self.raw_postings), dict(self.posting_fingerprints), dict(self.ingest_runs)
        )
        yield tx
        self.raw_postings = tx.raw_postings
        self.posting_fingerprints = tx.posting_fingerprints
        self.ingest_runs = tx.ingest_runs


class MemoryTx:
    def __init__(
        self,
        raw_postings: dict[RawKey, dict[str, Any]],
        posting_fingerprints: dict[tuple[str, str], Fingerprint],
        ingest_runs: dict[tuple[str, str], IngestRun],
    ) -> None:
        self.raw_postings = raw_postings
        self.posting_fingerprints = posting_fingerprints
        self.ingest_runs = ingest_runs

    def insert_raw_postings(self, postings: Sequence[RawPosting], load_id: UUID) -> int:
        inserted = 0
        for posting in postings:
            key = (posting.source, posting.source_job_id, posting.run_date, posting.parser_version)
            if key not in self.raw_postings:
                self.raw_postings[key] = raw_row(posting, load_id)
                inserted += 1
        return inserted

    def upsert_fingerprints(self, fingerprints: Sequence[Fingerprint]) -> None:
        for fp in fingerprints:
            key = (fp.source, fp.source_job_id)
            old = self.posting_fingerprints.get(key)
            if old is None or fp.last_seen_run_date >= old.last_seen_run_date:
                self.posting_fingerprints[key] = fp

    def upsert_ingest_run(self, run: IngestRun) -> None:
        key = (run.source, run.run_id)
        old = self.ingest_runs.get(key)
        if old is not None and old.status == "succeeded" and run.status == "failed":
            return
        # Stored as JSON and read back, like the jsonb columns
        self.ingest_runs[key] = IngestRun.model_validate_json(
            json.dumps(run.model_dump(mode="json"))
        )
