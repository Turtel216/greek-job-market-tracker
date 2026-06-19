# Loads the made-up postings in tests/dedup/sample_postings.csv into the raw
# and ops tables of a Postgres container. Source this file from a script
# that runs from the repo root, then call: load_sample <container name>
#
# Each row of the sample is one posting, seen every day from first_seen to
# last_seen. Both sources get a successful run every day of the sample.
# tests/dedup/make_sample.py says how the postings were made.

load_sample() {
    local container=$1
    local psql=(docker exec -i "$container" psql -q -v ON_ERROR_STOP=1 -U postgres -d warehouse)

    "${psql[@]}" -c "create table public.sample_postings (
        source text, source_job_id text, first_seen date, last_seen date, title text,
        company text, location text, workplace text, department text)"
    "${psql[@]}" -c "copy public.sample_postings from stdin with (format csv, header)" \
        < tests/dedup/sample_postings.csv
    "${psql[@]}" <<'SQL'
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

drop table public.sample_postings;
SQL
}
