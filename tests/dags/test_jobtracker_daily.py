from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from airflow.dag_processing.dagbag import DagBag
from airflow.sdk import CronTriggerTimetable, TriggerRule
from conftest import DAGS, DBT_STEPS

from common.cli import CliOperator
from common.defaults import HTTP_POOL

DBT_TASKS = ["dbt_freshness", "dbt_staging", "dbt_snapshot", "dbt_intermediate", "dbt_marts"]

REGISTRY = """
sources:
  - name: greenhouse
    kind: ats_api
    enabled: true
  - name: kariera
    kind: job_board_html
    enabled: false
  - name: lever
    kind: ats_api
    enabled: true
"""


def load_dags(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, registry: str) -> DagBag:
    path = tmp_path / "sources.yml"
    path.write_text(registry)
    monkeypatch.setenv("JOBTRACKER_SOURCES", str(path))
    # Tasks that use any other pool become import errors
    return DagBag(dag_folder=str(DAGS), known_pools={"default_pool", HTTP_POOL})


@pytest.fixture
def dagbag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> DagBag:
    return load_dags(tmp_path, monkeypatch, REGISTRY)


def callbacks(value: Callable[..., Any] | list[Callable[..., Any]] | None) -> list[str]:
    items = value if isinstance(value, list) else [value] if value else []
    return [f"{f.__module__}.{f.__qualname__}" for f in items]


def test_dags_import_without_errors(dagbag: DagBag) -> None:
    assert dagbag.import_errors == {}
    assert "jobtracker_daily" in dagbag.dag_ids


def test_every_task_sends_alerts(dagbag: DagBag) -> None:
    for dag in dagbag.dags.values():
        for task in dag.tasks:
            assert "common.alerts.task_failed" in callbacks(task.on_failure_callback), task
            assert "common.alerts.task_retried" in callbacks(task.on_retry_callback), task


def test_schedule_and_run_settings(dagbag: DagBag) -> None:
    dag = dagbag.dags["jobtracker_daily"]
    # A trigger timetable runs at 06:00 itself, not one interval later
    assert isinstance(dag.timetable, CronTriggerTimetable)
    assert dag.timetable.expression == "0 6 * * *"
    assert str(dag.timetable.timezone) == "Europe/Athens"
    assert dag.catchup is False
    assert dag.max_active_runs == 1


def test_one_task_group_per_enabled_source(dagbag: DagBag) -> None:
    dag = dagbag.dags["jobtracker_daily"]
    assert sorted(dag.task_ids) == sorted(
        [
            "greenhouse.extract",
            "greenhouse.load",
            "lever.extract",
            "lever.load",
            *DBT_TASKS,
            "notify_summary",
        ]
    )


def test_no_tasks_without_enabled_sources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Nothing is loaded, so dbt has nothing new to build either
    dagbag = load_dags(tmp_path, monkeypatch, "sources: []\n")
    assert dagbag.import_errors == {}
    assert dagbag.dags["jobtracker_daily"].task_ids == []


def test_extract_then_load(dagbag: DagBag) -> None:
    dag = dagbag.dags["jobtracker_daily"]
    for source in ("greenhouse", "lever"):
        extract = dag.get_task(f"{source}.extract")
        assert extract.downstream_task_ids == {f"{source}.load"}
        assert dag.get_task(f"{source}.load").downstream_task_ids == {"dbt_freshness"}


def test_dbt_steps_run_in_order_after_every_load(dagbag: DagBag) -> None:
    dag = dagbag.dags["jobtracker_daily"]
    assert dag.get_task("dbt_freshness").upstream_task_ids == {"greenhouse.load", "lever.load"}
    for before, after in pairwise(DBT_TASKS):
        assert dag.get_task(before).downstream_task_ids == {after}
    assert dag.get_task("dbt_marts").downstream_task_ids == {"notify_summary"}


def test_summary_runs_last_without_deciding_the_run(dagbag: DagBag) -> None:
    dag = dagbag.dags["jobtracker_daily"]
    summary = dag.get_task("notify_summary")
    assert summary.downstream_task_ids == set()
    # A teardown runs after a failure too, and does not count toward the
    # run's state, so a failed dbt step still fails the run
    assert summary.is_teardown
    assert summary.retries == 1


def test_summary_gets_the_athens_run_date(dagbag: DagBag) -> None:
    dag = dagbag.dags["jobtracker_daily"]
    task = dag.get_task("notify_summary")
    context = {"dag_run": SimpleNamespace(run_after=datetime(2026, 10, 24, 3, 0, tzinfo=UTC))}
    rendered = task.render_template(task.op_kwargs, context, dag.get_template_env())
    assert rendered == {"run_date": "2026-10-24"}


def test_dbt_starts_when_one_source_loaded(dagbag: DagBag) -> None:
    dag = dagbag.dags["jobtracker_daily"]
    # Waits for every load, and needs one of them to have succeeded
    assert dag.get_task("dbt_freshness").trigger_rule == TriggerRule.ALL_DONE_MIN_ONE_SUCCESS
    for task_id in DBT_TASKS[1:]:
        assert dag.get_task(task_id).trigger_rule == TriggerRule.ALL_SUCCESS


def test_dbt_retries_follow_the_design(dagbag: DagBag) -> None:
    dag = dagbag.dags["jobtracker_daily"]
    retries = {task_id: dag.get_task(task_id).retries for task_id in DBT_TASKS}
    assert retries == {
        "dbt_freshness": 0,
        "dbt_staging": 0,
        "dbt_snapshot": 1,
        "dbt_intermediate": 0,
        "dbt_marts": 0,
    }


def test_retries_and_pool_follow_the_design(dagbag: DagBag) -> None:
    dag = dagbag.dags["jobtracker_daily"]
    extract = dag.get_task("greenhouse.extract")
    load = dag.get_task("greenhouse.load")

    assert extract.pool == HTTP_POOL
    assert extract.retries == 2
    assert extract.retry_delay == timedelta(minutes=10)
    assert load.pool != HTTP_POOL
    assert load.retries == 1


@pytest.mark.parametrize(
    ("run_after", "run_date"),
    [
        # The scheduled run at 06:00 in Athens
        (datetime(2026, 10, 24, 3, 0, tzinfo=UTC), "2026-10-24"),
        # A manual run just after midnight in Athens
        (datetime(2026, 10, 24, 21, 30, tzinfo=UTC), "2026-10-25"),
    ],
)
def test_commands_get_the_athens_run_date(
    dagbag: DagBag, run_after: datetime, run_date: str
) -> None:
    dag = dagbag.dags["jobtracker_daily"]
    context = {"dag_run": SimpleNamespace(run_after=run_after)}

    for step in ("extract", "load"):
        task = dag.get_task(f"greenhouse.{step}")
        assert isinstance(task, CliOperator)
        rendered = task.render_template(task.command, context, dag.get_template_env())
        assert rendered == [
            "jobtracker",
            step,
            "--source",
            "greenhouse",
            "--run-date",
            run_date,
        ]


def render(dag: Any, task_id: str, run_after: datetime) -> list[str]:
    task = dag.get_task(task_id)
    assert isinstance(task, CliOperator)
    context = {"dag_run": SimpleNamespace(run_after=run_after)}
    return task.render_template(task.command, context, dag.get_template_env())


def test_dbt_steps_build_one_selector_each(dagbag: DagBag) -> None:
    dag = dagbag.dags["jobtracker_daily"]
    run_after = datetime(2026, 10, 24, 3, 0, tzinfo=UTC)
    builds = [render(dag, task_id, run_after) for task_id in DBT_TASKS[1:]]

    assert [command[command.index("--selector") + 1] for command in builds] == DBT_STEPS
    for command, step in zip(builds, DBT_STEPS, strict=True):
        assert command[:2] == ["dbt", "build"]
        # The DAG never runs dbt unit tests; CI does
        assert command[command.index("--exclude-resource-type") + 1] == "unit_test"
        folder = f"/opt/airflow/logs/dbt/2026-10-24/{step}"
        assert command[command.index("--target-path") + 1] == folder
        assert command[command.index("--log-path") + 1] == folder


def test_dbt_freshness_command(dagbag: DagBag) -> None:
    dag = dagbag.dags["jobtracker_daily"]
    command = render(dag, "dbt_freshness", datetime(2026, 10, 24, 3, 0, tzinfo=UTC))
    folder = "/opt/airflow/logs/dbt/2026-10-24/freshness"
    assert command == [
        "dbt",
        "source",
        "freshness",
        "--target-path",
        folder,
        "--log-path",
        folder,
    ]
