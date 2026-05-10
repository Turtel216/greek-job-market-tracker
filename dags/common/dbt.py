"""Commands for the dbt steps of the daily DAG.

dbt runs from its own venv in the Airflow image, which also holds the dbt
project and points DBT_PROJECT_DIR and DBT_PROFILES_DIR at it. Each step
builds one selector from transform/selectors.yml.

Every step keeps its artifacts, such as run_results.json and manifest.json,
and its dbt.log in a folder of its own per run_date on the logs volume, so
a failed run can be looked at later. Running a step again for the same
run_date replaces them.

dbt unit tests run in CI, not here. The SQL they test only changes with a
deploy, and CI has already run them by then.
"""

from common.rundate import RUN_DATE

# On the airflow-logs volume, so the artifacts survive a restart
ARTIFACTS_DIR = "/opt/airflow/logs/dbt"


def dbt_command(step: str, *args: str) -> list[str]:
    """A dbt command that writes its artifacts and log to the step's folder."""
    folder = f"{ARTIFACTS_DIR}/{RUN_DATE}/{step}"
    return ["dbt", *args, "--target-path", folder, "--log-path", folder]


def dbt_build(selector: str) -> list[str]:
    """Build one layer and run its tests."""
    return dbt_command(
        selector, "build", "--selector", selector, "--exclude-resource-type", "unit_test"
    )


def dbt_freshness() -> list[str]:
    """Check that every source loaded recently. Errors at 50 hours."""
    return dbt_command("freshness", "source", "freshness")
