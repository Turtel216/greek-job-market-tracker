#!/usr/bin/env bash
# Scores deduplication on the made-up postings in tests/dedup against the
# labels in transform/seeds/dedup_labels.csv, using a throwaway database.
# Fails when precision is under 0.95. Set DBT to use another dbt binary.
#
# Each row of the sample is one posting, seen every day from first_seen to
# last_seen. Both sources get a successful run every day of the sample.
# tests/dedup/make_sample.py says how the postings were made.
set -euo pipefail

cd "$(dirname "$0")/.."
DBT=${DBT:-$PWD/transform/.venv/bin/dbt}
source scripts/lib/throwaway-postgres.sh

psql_admin() {
    docker exec -i "$TEST_DB_NAME" psql -q -v ON_ERROR_STOP=1 -U postgres -d warehouse "$@"
}

psql_admin -c "create table public.sample_postings (
    source text, source_job_id text, first_seen date, last_seen date, title text,
    company text, location text, workplace text, department text)"
psql_admin -c "copy public.sample_postings from stdin with (format csv, header)" \
    < tests/dedup/sample_postings.csv
psql_admin <<'SQL'
insert into raw.raw_postings (
    source, source_job_id, run_date, scraped_at, url, title_raw, company_raw,
    location_raw, workplace_raw, department_raw, content_hash, parser_version, load_id
)
select
    source,
    source_job_id,
    run_date::date,
    run_date + interval '3 hours',
    'https://example.org/' || source || '/' || source_job_id,
    title,
    company,
    location,
    workplace,
    department,
    md5(concat_ws('|', title, company, location, workplace, department)),
    'sample',
    gen_random_uuid()
from public.sample_postings
cross join generate_series(first_seen, last_seen, interval '1 day') as run_date;

insert into ops.ingest_runs
select
    source, source || '-' || run_date::date, run_date::date, gen_random_uuid(),
    'succeeded', null, 'sample', 'sample', run_date, run_date, run_date, run_date,
    0, 0, 0, 0, 0, 0, 0, '{}', '{}'
from (select distinct source from public.sample_postings) as sources
cross join generate_series(
    (select min(first_seen) from public.sample_postings),
    (select max(last_seen) from public.sample_postings),
    interval '1 day'
) as run_date;
SQL

export POSTGRES_HOST=127.0.0.1
export POSTGRES_PORT="$TEST_DB_PORT"
export WAREHOUSE_DB=warehouse
export TRANSFORMER_PASSWORD="$TEST_DB_PASSWORD"

cd transform
"$DBT" build --exclude-resource-type unit_test --quiet
"$DBT" show --select dedup_precision --output json --quiet | python3 -c '
import json, sys

scores = json.load(sys.stdin)["show"][0]
for name, value in scores.items():
    print(f"{name:>18}: {value}")
if scores["precision_score"] is None or scores["precision_score"] < 0.95:
    sys.exit("Precision is under 0.95")
'
