"""The daily summary sent at the end of jobtracker_daily.

For each enabled source it gives the postings new in today's run, the ones
open now and the ones today's run closed, from the qa.qa_source_status
view. Then it lists every dbt test that warned or failed today, and the
dbt steps that left no results, from the artifacts each step keeps (see
common.dbt). It goes out on the same channels as the alerts.

It reads the warehouse as the read-only bi_reader role, through the
warehouse_reader connection, defined from the environment as
AIRFLOW_CONN_WAREHOUSE_READER.
"""

import json
from datetime import date
from pathlib import Path
from typing import NamedTuple

from common import alerts
from common.dbt import ARTIFACTS_DIR
from common.defaults import enabled_sources

WAREHOUSE_CONN_ID = "warehouse_reader"

# The dbt steps in the order they run, with the artifact each one leaves
STEPS = {
    "freshness": "sources.json",
    "staging": "run_results.json",
    "snapshots": "run_results.json",
    "intermediate": "run_results.json",
    "marts": "run_results.json",
}
# Results worth telling about. Passing and skipped ones are left out.
PROBLEMS = {"warn", "fail", "error", "runtime error"}
# Keeps the message well under the Discord limit
MAX_PROBLEMS = 20


class SourceStatus(NamedTuple):
    last_run_date: date
    new_postings: int
    open_postings: int
    closed_postings: int


class DbtReport(NamedTuple):
    # Lines such as "warn: volume_vs_7d_avg_stg_raw_postings_run_date__source"
    problems: list[str]
    # Steps with no artifact, because they did not run or crashed first
    missing_steps: list[str]


def notify_summary(run_date: str) -> None:
    message = summary_message(
        run_date, enabled_sources(), source_status(), dbt_report(Path(ARTIFACTS_DIR) / run_date)
    )
    alerts.send(message)


def source_status() -> dict[str, SourceStatus]:
    # Imported here so a DAG file does not pay for it at parse time
    from airflow.providers.postgres.hooks.postgres import PostgresHook

    rows = PostgresHook(postgres_conn_id=WAREHOUSE_CONN_ID).get_records(
        "select source, last_run_date, new_postings, open_postings, closed_postings"
        " from qa.qa_source_status"
    )
    return {row[0]: SourceStatus(*row[1:]) for row in rows}


def dbt_report(folder: Path) -> DbtReport:
    """What went wrong in the dbt steps of one run_date."""
    problems = []
    missing = []
    for step, artifact in STEPS.items():
        path = folder / step / artifact
        if not path.exists():
            missing.append(step)
            continue
        for result in json.loads(path.read_text())["results"]:
            if result["status"] in PROBLEMS:
                problems.append(f"{result['status']}: {node_name(result['unique_id'])}")
    return DbtReport(problems, missing)


def node_name(unique_id: str) -> str:
    """test.jobtracker.name.hash becomes name, source.jobtracker.raw.table raw.table."""
    kind, _, *rest = unique_id.split(".")
    return rest[0] if kind == "test" else ".".join(rest)


def summary_message(
    run_date: str, sources: list[str], status: dict[str, SourceStatus], report: DbtReport
) -> str:
    lines = [f"Daily summary for {run_date}"]
    for source in sources:
        current = status.get(source)
        if current is None:
            lines.append(f"{source}: no successful run yet")
        elif current.last_run_date.isoformat() != run_date:
            lines.append(f"{source}: no successful run today, last on {current.last_run_date}")
        else:
            lines.append(
                f"{source}: {current.new_postings} new, {current.closed_postings} closed, "
                f"{current.open_postings} open"
            )

    if report.missing_steps:
        lines.append(
            f"dbt steps with no results: {', '.join(report.missing_steps)}. "
            "The counts above may be from an earlier build."
        )
    if report.problems:
        lines.append("dbt warnings and failures:")
        lines.extend(f"- {problem}" for problem in report.problems[:MAX_PROBLEMS])
        if len(report.problems) > MAX_PROBLEMS:
            lines.append(f"- and {len(report.problems) - MAX_PROBLEMS} more")
    elif not report.missing_steps:
        lines.append("dbt: every step ran with no warnings")
    return "\n".join(lines)
