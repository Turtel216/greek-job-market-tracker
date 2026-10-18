-- What did not pass in the newest build of each dbt step
with

newest as (
    select distinct on (step)
        step,
        invocation_id
    from ops.dbt_runs
    order by step, generated_at desc
)

select
    dbt_runs.status,
    dbt_runs.step,
    dbt_runs.name,
    dbt_runs.failures,
    dbt_runs.message,
    dbt_runs.resource_type,
    dbt_runs.run_date
from ops.dbt_runs
inner join newest on dbt_runs.invocation_id = newest.invocation_id
where dbt_runs.status not in ('pass', 'success')
order by
    array_position(array['freshness', 'staging', 'snapshots', 'intermediate', 'marts'], dbt_runs.step),
    dbt_runs.status,
    dbt_runs.name
