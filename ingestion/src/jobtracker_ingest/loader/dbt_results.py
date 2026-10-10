"""Reading dbt artifacts into rows of ops.dbt_runs.

Each dbt step of a DAG run keeps its artifacts in a folder of its own,
named after the step (see dags/common/dbt.py). A build leaves
run_results.json and the freshness check leaves sources.json. Both list one
result per node, under the invocation_id of the dbt command.

    <folder>/staging/run_results.json
    <folder>/freshness/sources.json

A step folder without an artifact is skipped, since that step did not run
or crashed before dbt wrote one.
"""

import json
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

from jobtracker_ingest.loader.models import DbtResult
from jobtracker_ingest.loader.warehouse import Warehouse

ARTIFACTS = ("run_results.json", "sources.json")


def node_name(unique_id: str) -> str:
    """A short name for a node.

    test.jobtracker.name.hash becomes name, source.jobtracker.raw.table
    becomes raw.table, and model.jobtracker.name or
    unit_test.jobtracker.model.name become name.
    """
    kind, _, *rest = unique_id.split(".")
    if kind == "test":
        return rest[0]
    if kind == "source":
        return ".".join(rest)
    return rest[-1]


def result_row(
    result: dict[str, Any], metadata: dict[str, Any], step: str, run_date: date
) -> DbtResult:
    unique_id = result["unique_id"]
    adapter_response = result.get("adapter_response") or {}
    rows_affected = adapter_response.get("rows_affected")
    return DbtResult(
        invocation_id=metadata["invocation_id"],
        unique_id=unique_id,
        run_date=run_date,
        step=step,
        resource_type=unique_id.split(".")[0],
        name=node_name(unique_id),
        status=result["status"],
        failures=result.get("failures"),
        # A freshness check that could not run gives its reason as error
        message=result.get("message") or result.get("error"),
        # Postgres gives -1 for statements that report no row count
        rows_affected=rows_affected if rows_affected is not None and rows_affected >= 0 else None,
        execution_time=result.get("execution_time"),
        generated_at=metadata["generated_at"],
    )


def read_artifact(path: Path, step: str, run_date: date) -> Iterator[DbtResult]:
    artifact = json.loads(path.read_text())
    for result in artifact["results"]:
        yield result_row(result, artifact["metadata"], step, run_date)


def read_dbt_results(folder: Path, run_date: date) -> list[DbtResult]:
    """Every result in the step folders under folder, in step and node order."""
    rows: dict[tuple[str, str], DbtResult] = {}
    for step_folder in sorted(p for p in folder.iterdir() if p.is_dir()):
        for artifact in ARTIFACTS:
            path = step_folder / artifact
            if not path.exists():
                continue
            for row in read_artifact(path, step_folder.name, run_date):
                # One row per node and invocation, like the table's key
                rows[(str(row.invocation_id), row.unique_id)] = row
    return list(rows.values())


def load_dbt_results(folder: Path, run_date: date, warehouse: Warehouse) -> int:
    """Write every result under folder to ops.dbt_runs and return how many."""
    rows = read_dbt_results(folder, run_date)
    with warehouse.transaction() as tx:
        tx.upsert_dbt_results(rows)
    return len(rows)
