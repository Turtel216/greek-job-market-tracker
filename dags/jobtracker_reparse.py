"""Reparse: load stored payloads again after a parser fix, then rebuild dbt.

Triggered by hand with a source and a date range, once the parser fix is
deployed with a new PARSER_VERSION. `jobtracker reparse` loads the newest
complete run of each day in the range from the object store, without
scraping again. Then dbt builds every layer as the daily run does, so the
marts pick up the fixed rows.

Payloads are kept for 30 days, so older days cannot be reparsed. Their raw
rows stay as they are.

The dbt steps share the dbt pool with jobtracker_daily, so two builds never
run at once. They keep their artifacts in a folder per reparse run, apart
from the daily ones the summary reads. Source freshness is not checked,
since a reparse fetches nothing new.
"""

from datetime import datetime

from airflow.sdk import DAG, Param

from common.cli import CliOperator
from common.dbt import REPARSE_FOLDER, dbt_build
from common.defaults import DBT_POOL, DEFAULT_ARGS, all_sources

# Every source in the registry, since a disabled one may still need its
# stored runs loaded again
SOURCES = all_sources()

with DAG(
    dag_id="jobtracker_reparse",
    description="Load stored payloads again after a parser fix, then rebuild dbt",
    schedule=None,
    start_date=datetime(2026, 10, 1),
    catchup=False,
    max_active_runs=1,
    default_args=DEFAULT_ARGS,
    params={
        "source": Param(
            type="string",
            description="The source whose parser was fixed",
            # An empty enum is not valid JSON schema
            **({"enum": SOURCES} if SOURCES else {}),
        ),
        "start_date": Param(
            type="string", format="date", description="First run_date to load again"
        ),
        "end_date": Param(
            type="string", format="date", description="Last run_date to load again, inclusive"
        ),
    },
    doc_md=__doc__,
    tags=["jobtracker"],
):
    reparse = CliOperator(
        task_id="reparse",
        command=[
            "jobtracker",
            "reparse",
            "--source",
            "{{ params.source }}",
            "--start-date",
            "{{ params.start_date }}",
            "--end-date",
            "{{ params.end_date }}",
        ],
    )
    staging = CliOperator(
        task_id="dbt_staging", command=dbt_build("staging", REPARSE_FOLDER), pool=DBT_POOL
    )
    snapshot = CliOperator(
        task_id="dbt_snapshot",
        command=dbt_build("snapshots", REPARSE_FOLDER),
        retries=1,
        pool=DBT_POOL,
    )
    intermediate = CliOperator(
        task_id="dbt_intermediate",
        command=dbt_build("intermediate", REPARSE_FOLDER),
        pool=DBT_POOL,
    )
    marts = CliOperator(
        task_id="dbt_marts", command=dbt_build("marts", REPARSE_FOLDER), pool=DBT_POOL
    )
    reparse >> staging >> snapshot >> intermediate >> marts
