-- Data tests and freshness checks by result, for the newest build of each
-- dbt step on each run_date over the last 30 days
with

newest as (
    select distinct on (run_date, step)
        run_date,
        step,
        invocation_id
    from ops.dbt_runs
    where run_date > current_date - 30
    order by run_date, step, generated_at desc
)

select
    newest.run_date,
    dbt_runs.status,
    count(*) as results
from ops.dbt_runs
inner join newest on dbt_runs.invocation_id = newest.invocation_id
where dbt_runs.resource_type in ('test', 'source')
group by newest.run_date, dbt_runs.status
order by newest.run_date, dbt_runs.status
