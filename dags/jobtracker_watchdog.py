"""Watchdog: alert when today's jobtracker_daily run has not succeeded by 10:00.

The daily run starts at 06:00 and normally finishes well before 10:00. If it
failed, is still running or never started, the check fails and the failure
alert says so, with the last run_date that succeeded.
"""

from datetime import datetime

from airflow.providers.standard.operators.python import PythonOperator
from airflow.sdk import DAG, CronTriggerTimetable

from common.defaults import DEFAULT_ARGS
from common.rundate import RUN_DATE, TIMEZONE, athens_date
from common.watchdog import check_daily_run

with DAG(
    dag_id="jobtracker_watchdog",
    description="Alert if today's daily run has not succeeded",
    schedule=CronTriggerTimetable("0 10 * * *", timezone=TIMEZONE),
    start_date=datetime(2026, 10, 1),
    catchup=False,
    max_active_runs=1,
    default_args=DEFAULT_ARGS,
    user_defined_macros={"athens_date": athens_date},
    doc_md=__doc__,
    tags=["jobtracker"],
):
    PythonOperator(
        task_id="check_daily_run",
        python_callable=check_daily_run,
        op_kwargs={"run_date": RUN_DATE},
    )
