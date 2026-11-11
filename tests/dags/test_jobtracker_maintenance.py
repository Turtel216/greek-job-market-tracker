from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from airflow.dag_processing.dagbag import DagBag
from airflow.sdk import CronTriggerTimetable
from conftest import DAGS

from common.cli import CliOperator
from common.defaults import DBT_POOL, HTTP_POOL

TASKS = ["backup_metabase", "backup_warehouse", "prune_raw"]


@pytest.fixture
def dag(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("WAREHOUSE_DB", "warehouse_test")
    dagbag = DagBag(dag_folder=str(DAGS), known_pools={"default_pool", HTTP_POOL, DBT_POOL})
    assert dagbag.import_errors == {}
    return dagbag.dags["jobtracker_maintenance"]


def render(dag: Any, task_id: str) -> list[str]:
    task = dag.get_task(task_id)
    assert isinstance(task, CliOperator)
    # 03:00 in Athens on 2026-11-28 is still 2026-11-28, though UTC says 01:00
    context = {"dag_run": SimpleNamespace(run_after=datetime(2026, 11, 28, 1, 0, tzinfo=UTC))}
    return task.render_template(task.command, context, dag.get_template_env())


def test_runs_at_three_one_at_a_time(dag: Any) -> None:
    assert isinstance(dag.timetable, CronTriggerTimetable)
    assert dag.timetable.expression == "0 3 * * *"
    assert str(dag.timetable.timezone) == "Europe/Athens"
    assert dag.catchup is False
    assert dag.max_active_runs == 1


def test_tasks_run_independently(dag: Any) -> None:
    assert sorted(dag.task_ids) == TASKS
    for task_id in TASKS:
        task = dag.get_task(task_id)
        assert task.upstream_task_ids == set()
        assert task.retries == 1


def test_prune_raw_gets_the_athens_run_date(dag: Any) -> None:
    assert render(dag, "prune_raw") == ["jobtracker", "prune-raw", "--run-date", "2026-11-28"]


@pytest.mark.parametrize(
    ("task_id", "database"),
    [("backup_warehouse", "warehouse_test"), ("backup_metabase", "metabase_app")],
)
def test_backups_dump_each_database_to_the_backup_folder(
    dag: Any, task_id: str, database: str
) -> None:
    assert render(dag, task_id) == [
        "jobtracker",
        "backup",
        "--database",
        database,
        "--run-date",
        "2026-11-28",
        "--folder",
        "/opt/airflow/backups",
    ]
