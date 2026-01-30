"""Daily pipeline: extract and load every enabled source.

There is one task group per enabled source in config/sources.yml, so adding
a source needs no change here. Sources run in parallel, and the extract
tasks share the http_sources pool, which caps how many are fetched at once.

Each task calls the jobtracker CLI with an explicit run_date. The dbt steps
come with the dbt project in Phase 3.
"""

from datetime import datetime, timedelta

from airflow.sdk import DAG, CronTriggerTimetable, TaskGroup

from common.cli import CliOperator
from common.defaults import DEFAULT_ARGS, HTTP_POOL, enabled_sources
from common.rundate import RUN_DATE, TIMEZONE, athens_date

with DAG(
    dag_id="jobtracker_daily",
    description="Extract, load, transform, test, publish",
    schedule=CronTriggerTimetable("0 6 * * *", timezone=TIMEZONE),
    start_date=datetime(2026, 10, 1),
    catchup=False,
    max_active_runs=1,
    default_args=DEFAULT_ARGS,
    user_defined_macros={"athens_date": athens_date},
    doc_md=__doc__,
    tags=["jobtracker"],
):
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
