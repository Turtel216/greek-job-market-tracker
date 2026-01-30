from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from airflow.dag_processing.dagbag import DagBag
from airflow.sdk import CronTriggerTimetable
from conftest import DAGS

from common.cli import CliOperator
from common.defaults import HTTP_POOL

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
    assert sorted(dag.task_ids) == [
        "greenhouse.extract",
        "greenhouse.load",
        "lever.extract",
        "lever.load",
    ]


def test_no_tasks_without_enabled_sources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dagbag = load_dags(tmp_path, monkeypatch, "sources: []\n")
    assert dagbag.import_errors == {}
    assert dagbag.dags["jobtracker_daily"].task_ids == []


def test_extract_then_load(dagbag: DagBag) -> None:
    dag = dagbag.dags["jobtracker_daily"]
    for source in ("greenhouse", "lever"):
        extract = dag.get_task(f"{source}.extract")
        assert extract.downstream_task_ids == {f"{source}.load"}
        assert dag.get_task(f"{source}.load").downstream_task_ids == set()


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
