-- Every load of the last 30 days, newest first
select
    run_date,
    source,
    status,
    postings_listed,
    rows_parsed,
    rows_inserted,
    details_reused,
    details_missing,
    parse_errors,
    extract_errors,
    round((extract(epoch from extract_finished_at - extract_started_at) / 60)::numeric, 1)
        as extract_minutes,
    parser_version,
    error
from ops.ingest_runs
where run_date > current_date - 30
order by run_date desc, source asc, load_finished_at desc
