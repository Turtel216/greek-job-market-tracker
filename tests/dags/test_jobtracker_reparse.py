from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest
from airflow.dag_processing.dagbag import DagBag
from conftest import DAGS, DBT_STEPS

from common.cli import CliOperator
from common.defaults import DBT_POOL, HTTP_POOL

TASKS = ["reparse", "dbt_staging", "dbt_snapshot", "dbt_intermediate", "dbt_marts"]

REGISTRY = """
sources:
  - name: greenhouse
    kind: ats_api
    enabled: true
  - name: kariera
    kind: job_board_html
    enabled: false
"""

PARAMS = {"source": "kariera", "start_date": "2026-10-20", "end_date": "2026-10-25"}
RUN_ID = "manual__2026-10-28T10:00:00+00:00"


def load_dags(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, registry: str) -> DagBag:
    path = tmp_path / "sources.yml"
    path.write_text(registry)
    monkeypatch.setenv("JOBTRACKER_SOURCES", str(path))
    return DagBag(dag_folder=str(DAGS), known_pools={"default_pool", HTTP_POOL, DBT_POOL})


@pytest.fixture
def dag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    dagbag = load_dags(tmp_path, monkeypatch, REGISTRY)
    assert dagbag.import_errors == {}
    return dagbag.dags["jobtracker_reparse"]


def render(dag: Any, task_id: str) -> list[str]:
    task = dag.get_task(task_id)
    assert isinstance(task, CliOperator)
    context = {"params": PARAMS, "run_id": RUN_ID}
    return task.render_template(task.command, context, dag.get_template_env())


def test_runs_only_by_hand_one_at_a_time(dag: Any) -> None:
    assert dag.schedule is None
    assert dag.catchup is False
    assert dag.max_active_runs == 1


def test_params_name_a_source_and_a_date_range(dag: Any) -> None:
    schemas = {name: param.schema for name, param in dag.params.items()}
    # A disabled source can be reparsed too
    assert schemas["source"]["enum"] == ["greenhouse", "kariera"]
    assert schemas["start_date"]["format"] == "date"
    assert schemas["end_date"]["format"] == "date"
    # No defaults, so every trigger has to fill them in
    assert not any(param.has_value for param in dag.params.values())


def test_imports_with_an_empty_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dagbag = load_dags(tmp_path, monkeypatch, "sources: []\n")
    assert dagbag.import_errors == {}
    source = dagbag.dags["jobtracker_reparse"].params.get_param("source")
    assert "enum" not in source.schema


def test_reparse_then_dbt_in_order(dag: Any) -> None:
    assert sorted(dag.task_ids) == sorted(TASKS)
    for before, after in pairwise(TASKS):
        assert dag.get_task(before).downstream_task_ids == {after}


def test_reparse_command_gets_the_params(dag: Any) -> None:
    assert render(dag, "reparse") == [
        "jobtracker",
        "reparse",
        "--source",
        "kariera",
        "--start-date",
        "2026-10-20",
        "--end-date",
        "2026-10-25",
    ]


def test_dbt_steps_keep_their_artifacts_apart_from_the_daily_ones(dag: Any) -> None:
    builds = [render(dag, task_id) for task_id in TASKS[1:]]

    assert [command[command.index("--selector") + 1] for command in builds] == DBT_STEPS
    for command, step in zip(builds, DBT_STEPS, strict=True):
        assert command[:2] == ["dbt", "build"]
        assert command[command.index("--exclude-resource-type") + 1] == "unit_test"
        folder = f"/opt/airflow/logs/dbt/reparse/{RUN_ID}/{step}"
        assert command[command.index("--target-path") + 1] == folder
        assert command[command.index("--log-path") + 1] == folder


def test_dbt_steps_share_the_dbt_pool_and_retries(dag: Any) -> None:
    assert {dag.get_task(task_id).pool for task_id in TASKS[1:]} == {DBT_POOL}
    assert dag.get_task("reparse").pool != DBT_POOL
    retries = {task_id: dag.get_task(task_id).retries for task_id in TASKS}
    assert retries == {
        "reparse": 0,
        "dbt_staging": 0,
        "dbt_snapshot": 1,
        "dbt_intermediate": 0,
        "dbt_marts": 0,
    }
