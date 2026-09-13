-- Tables in raw and ops, written by the loader. See "Data model" in the
-- design doc. The postgres image runs this once on an empty volume. It is
-- safe to run again, so `make db-tables` applies it to a running stack.
--
-- The admin role creates these tables, so the default privileges from
-- 02-warehouse.sh apply: the loader can only insert into raw, and can
-- read, insert and update ops.

-- One row per posting, source, run_date and parser version. Never updated
-- or deleted. A reparse with a new parser version adds rows next to the
-- old ones, and staging reads the newest.
create table if not exists raw.raw_postings (
    raw_posting_id bigint generated always as identity primary key,
    source text not null,
    source_job_id text not null,
    run_date date not null,
    scraped_at timestamptz not null,
    url text not null,
    title_raw text,
    company_raw text,
    location_raw text,
    workplace_raw text,
    employment_type_raw text,
    salary_raw text,
    department_raw text,
    description_raw text,
    posted_at_raw text,
    content_hash text not null,
    object_key text,
    parser_version text not null,
    load_id uuid not null
);

-- The natural key. Older databases have it without parser_version, which
-- stops a reparse from adding rows, so that constraint is replaced.
alter table raw.raw_postings
    drop constraint if exists raw_postings_source_source_job_id_run_date_key;
create unique index if not exists raw_postings_natural_key
    on raw.raw_postings (source, source_job_id, run_date, parser_version);

-- One row per extract run that was loaded. Loading the same run again
-- replaces its row, so a retried load leaves the same end state.
create table if not exists ops.ingest_runs (
    source text not null,
    run_id text not null,
    run_date date not null,
    load_id uuid not null,
    status text not null check (status in ('succeeded', 'failed')),
    error text,
    adapter_version text not null,
    parser_version text not null,
    extract_started_at timestamptz not null,
    extract_finished_at timestamptz not null,
    load_started_at timestamptz not null,
    load_finished_at timestamptz not null,
    postings_listed integer not null,
    rows_parsed integer not null,
    rows_inserted integer not null,
    details_reused integer not null,
    details_missing integer not null,
    parse_errors integer not null,
    extract_errors integer not null,
    extract_counts jsonb not null,
    http_stats jsonb not null,
    primary key (source, run_id)
);

create index if not exists ingest_runs_source_run_date
    on ops.ingest_runs (source, run_date);

-- One row per posting: the last listing fingerprint and the last stored
-- detail. Extract compares the fingerprint to decide whether to fetch the
-- detail again, and the loader reuses the stored detail when it did not.
create table if not exists ops.posting_fingerprints (
    source text not null,
    source_job_id text not null,
    listing_fingerprint text not null,
    account text,
    url text not null,
    last_seen_run_date date not null,
    detail_object_key text not null,
    detail_url text not null,
    detail_content_type text not null,
    detail_fetched_at timestamptz not null,
    content_hash text not null,
    updated_at timestamptz not null default now(),
    primary key (source, source_job_id)
);
