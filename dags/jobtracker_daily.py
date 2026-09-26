"""Daily pipeline: extract and load every enabled source, then build dbt.

There is one task group per enabled source in config/sources.yml, so adding
a source needs no change here. Sources run in parallel, and the extract
tasks share the http_sources pool, which caps how many are fetched at once.

Once every load is done, dbt checks source freshness and then builds one
layer at a time: staging, snapshots, intermediate and marts. Each step runs
its layer's tests, so a failed test keeps the marts on the last good state.
The dbt steps share the dbt pool with jobtracker_reparse, so the two never
build at the same time.
dbt starts when at least one source loaded, so one broken source never
blocks the others. When none did, the dbt steps do not run at all.

Last, notify_summary sends the day's counts per source and any dbt warnings.
It is a teardown task, so it runs even when an earlier task failed, and it
never changes whether the run succeeded. The watchdog relies on that.

Each task calls a CLI command with an explicit run_date.
"""

from datetime import datetime, timedelta

from airflow.providers.standard.operators.python import PythonOperator
from airflow.sdk import DAG, CronTriggerTimetable, TaskGroup, TriggerRule

from common.cli import CliOperator
from common.dbt import dbt_build, dbt_freshness
from common.defaults import DAILY_RUN_HOUR, DBT_POOL, DEFAULT_ARGS, HTTP_POOL, enabled_sources
from common.rundate import RUN_DATE, TIMEZONE, athens_date
from common.summary import notify_summary

with DAG(
    dag_id="jobtracker_daily",
    description="Extract, load, transform, test, publish",
    schedule=CronTriggerTimetable(f"0 {DAILY_RUN_HOUR} * * *", timezone=TIMEZONE),
    start_date=datetime(2026, 10, 1),
    catchup=False,
    max_active_runs=1,
    default_args=DEFAULT_ARGS,
    user_defined_macros={"athens_date": athens_date},
    doc_md=__doc__,
    tags=["jobtracker"],
):
    loads = []
    for source in enabled_sources():
        with TaskGroup(group_id=source):
            extract = CliOperator(
                task_id="extract",
                command=["jobtracker", "extract", "--source", source, "--run-date", RUN_DATE],
                pool=HTTP_POOL,
                retries=2,
                retry_delay=timedelta(minutes=10),
            )
            load = CliOperator(
                task_id="load",
                command=["jobtracker", "load", "--source", source, "--run-date", RUN_DATE],
                retries=1,
            )
            extract >> load
            loads.append(load)

    # With no source enabled there is nothing new to transform
    if loads:
        freshness = CliOperator(
            task_id="dbt_freshness",
            command=dbt_freshness(),
            trigger_rule=TriggerRule.ALL_DONE_MIN_ONE_SUCCESS,
            pool=DBT_POOL,
        )
        staging = CliOperator(task_id="dbt_staging", command=dbt_build("staging"), pool=DBT_POOL)
        snapshot = CliOperator(
            task_id="dbt_snapshot", command=dbt_build("snapshots"), retries=1, pool=DBT_POOL
        )
        intermediate = CliOperator(
            task_id="dbt_intermediate", command=dbt_build("intermediate"), pool=DBT_POOL
        )
        marts = CliOperator(task_id="dbt_marts", command=dbt_build("marts"), pool=DBT_POOL)
        summary = PythonOperator(
            task_id="notify_summary",
            python_callable=notify_summary,
            op_kwargs={"run_date": RUN_DATE},
            retries=1,
        )
        loads >> freshness >> staging >> snapshot >> intermediate >> marts
        marts >> summary.as_teardown()
