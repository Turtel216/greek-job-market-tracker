"""Commands for the dbt steps of the daily and reparse DAGs.

dbt runs from its own venv in the Airflow image, which also holds the dbt
project and points DBT_PROJECT_DIR and DBT_PROFILES_DIR at it. Each step
builds one selector from transform/selectors.yml.

Every step keeps its artifacts, such as run_results.json and manifest.json,
and its dbt.log in a folder of its own on the logs volume, so a failed run
can be looked at later. The daily DAG uses one folder per run_date, and
running a step again for the same run_date replaces them. A reparse uses
one folder per DAG run, so it never mixes with the daily artifacts the
summary reads.

dbt unit tests run in CI, not here. The SQL they test only changes with a
deploy, and CI has already run them by then.
"""

from common.rundate import RUN_DATE

# On the airflow-logs volume, so the artifacts survive a restart
ARTIFACTS_DIR = "/opt/airflow/logs/dbt"
DAILY_FOLDER = f"{ARTIFACTS_DIR}/{RUN_DATE}"
REPARSE_FOLDER = f"{ARTIFACTS_DIR}/reparse/{{{{ run_id }}}}"


def dbt_command(step: str, *args: str, folder: str = DAILY_FOLDER) -> list[str]:
    """A dbt command that writes its artifacts and log to the step's folder."""
    step_folder = f"{folder}/{step}"
    return ["dbt", *args, "--target-path", step_folder, "--log-path", step_folder]


def dbt_build(selector: str, folder: str = DAILY_FOLDER) -> list[str]:
    """Build one layer and run its tests."""
    return dbt_command(
        selector,
        "build",
        "--selector",
        selector,
        "--exclude-resource-type",
        "unit_test",
        folder=folder,
    )


def dbt_freshness() -> list[str]:
    """Check that every source loaded recently. Errors at 50 hours."""
    return dbt_command("freshness", "source", "freshness")
