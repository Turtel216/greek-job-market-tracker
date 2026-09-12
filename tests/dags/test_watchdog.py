from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from airflow.dag_processing.dagbag import DagBag
from airflow.sdk import CronTriggerTimetable
from conftest import DAGS

from common.watchdog import DAILY_DAG_ID, DailyRunMissing, check_daily_run


class FakeTaskInstance:
    """Answers get_dr_count from a map of daily runs to their state."""

    def __init__(self, runs: dict[datetime, str]) -> None:
        self.runs = runs

    def get_dr_count(self, dag_id: str, logical_dates: list[datetime], states: list[str]) -> int:
        assert dag_id == DAILY_DAG_ID
        return sum(self.runs.get(when) in states for when in logical_dates)


def at_six(day: int) -> datetime:
    # 06:00 in Athens in October 2026, before summer time ends
    return datetime(2026, 10, day, 3, 0, tzinfo=UTC)


def test_passes_when_todays_run_succeeded() -> None:
    check_daily_run("2026-10-24", FakeTaskInstance({at_six(24): "success"}))


def test_fails_with_the_state_and_last_success() -> None:
    ti = FakeTaskInstance({at_six(24): "failed", at_six(23): "failed", at_six(22): "success"})
    with pytest.raises(DailyRunMissing) as error:
        check_daily_run("2026-10-24", ti)
    assert "today's run is failed" in str(error.value)
    assert "Last successful run_date: 2026-10-22" in str(error.value)


def test_a_run_still_going_is_not_a_success() -> None:
    ti = FakeTaskInstance({at_six(24): "running", at_six(23): "success"})
    with pytest.raises(DailyRunMissing, match="today's run is running"):
        check_daily_run("2026-10-24", ti)


def test_no_run_at_all() -> None:
    with pytest.raises(DailyRunMissing) as error:
        check_daily_run("2026-10-24", FakeTaskInstance({}))
    assert "today's run is missing" in str(error.value)
    assert "none in the last 30 days" in str(error.value)


def test_a_run_at_another_time_does_not_count() -> None:
    # A manual run at 08:00 is not today's scheduled run
    manual = datetime(2026, 10, 24, 5, 0, tzinfo=UTC)
    with pytest.raises(DailyRunMissing):
        check_daily_run("2026-10-24", FakeTaskInstance({manual: "success"}))


@pytest.fixture
def dagbag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> DagBag:
    path = tmp_path / "sources.yml"
    path.write_text("sources: []\n")
    monkeypatch.setenv("JOBTRACKER_SOURCES", str(path))
    return DagBag(dag_folder=str(DAGS))


def test_watchdog_runs_at_ten(dagbag: DagBag) -> None:
    assert dagbag.import_errors == {}
    dag = dagbag.dags["jobtracker_watchdog"]
    assert isinstance(dag.timetable, CronTriggerTimetable)
    assert dag.timetable.expression == "0 10 * * *"
    assert str(dag.timetable.timezone) == "Europe/Athens"
    assert dag.catchup is False
    assert dag.task_ids == ["check_daily_run"]


def test_watchdog_checks_the_athens_run_date(dagbag: DagBag) -> None:
    dag = dagbag.dags["jobtracker_watchdog"]
    task = dag.get_task("check_daily_run")
    # 10:00 in Athens
    context = {"dag_run": SimpleNamespace(run_after=datetime(2026, 10, 24, 7, 0, tzinfo=UTC))}
    rendered = task.render_template(task.op_kwargs, context, dag.get_template_env())
    assert rendered == {"run_date": "2026-10-24"}
