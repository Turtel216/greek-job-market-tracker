"""Maintenance: raw payload retention and database backups, every night.

prune_raw deletes raw payloads more than 30 days old from the object store.
Raw payloads may still hold personal data, so they are not kept longer. It
keeps an older stored detail when a posting seen in the last 30 days still
reuses it.

backup_warehouse and backup_metabase dump the two databases with pg_dump
into the backup folder, which is BACKUP_DIR on the host, and prune old
dumps down to the 14 newest and one for each of the 8 newest weeks. A dump
under half the size of the one before fails the task and prunes nothing.

The tasks do not depend on each other, so one failing never stops the
others. Each calls a CLI command with an explicit run_date. The DAG runs at
03:00, so it is done well before jobtracker_daily starts at 06:00.
"""

import os
from datetime import datetime

from airflow.sdk import DAG, CronTriggerTimetable

from common.cli import CliOperator
from common.defaults import BACKUP_FOLDER, DEFAULT_ARGS, MAINTENANCE_HOUR
from common.rundate import RUN_DATE, TIMEZONE, athens_date

# Task id to database. The warehouse name comes from the environment, like
# every other connection setting.
DATABASES = {
    "backup_warehouse": os.environ.get("WAREHOUSE_DB", "warehouse"),
    "backup_metabase": "metabase_app",
}

with DAG(
    dag_id="jobtracker_maintenance",
    description="Delete raw payloads after 30 days, back up the databases",
    schedule=CronTriggerTimetable(f"0 {MAINTENANCE_HOUR} * * *", timezone=TIMEZONE),
    start_date=datetime(2026, 10, 1),
    catchup=False,
    max_active_runs=1,
    default_args=DEFAULT_ARGS,
    user_defined_macros={"athens_date": athens_date},
    doc_md=__doc__,
    tags=["jobtracker"],
):
    CliOperator(
        task_id="prune_raw",
        command=["jobtracker", "prune-raw", "--run-date", RUN_DATE],
        retries=1,
    )
    for task_id, database in DATABASES.items():
        CliOperator(
            task_id=task_id,
            command=[
                "jobtracker",
                "backup",
                "--database",
                database,
                "--run-date",
                RUN_DATE,
                "--folder",
                BACKUP_FOLDER,
            ],
            retries=1,
        )
