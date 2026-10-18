-- Tests and nodes that did not pass in the newest build of each dbt step
with

newest as (
    select distinct on (step)
        step,
        invocation_id
    from ops.dbt_runs
    order by step, generated_at desc
)

select count(*) as problems
from ops.dbt_runs
inner join newest on dbt_runs.invocation_id = newest.invocation_id
where dbt_runs.status not in ('pass', 'success')
