"""The check behind jobtracker_watchdog.

It looks for today's scheduled run of jobtracker_daily and fails when that
run has not succeeded, so the failure alert goes out with the run's state
and the last run_date that did succeed. Clearing the failed tasks of
today's run and running them again counts. A separate manual run does not,
since it is not the run scheduled for today.
"""

from datetime import date, timedelta
from typing import Any

from airflow.sdk.exceptions import AirflowFailException

from common.defaults import DAILY_RUN_HOUR
from common.rundate import athens_time

DAILY_DAG_ID = "jobtracker_daily"
# How far back to look for the last successful run
LOOKBACK_DAYS = 30


class DailyRunMissing(AirflowFailException):
    """Today's daily run has not succeeded. Not retried."""


def check_daily_run(run_date: str, ti: Any) -> None:
    if succeeded(ti, run_date):
        return
    last = last_success(ti, run_date)
    raise DailyRunMissing(
        f"{DAILY_DAG_ID} has not succeeded for {run_date}, "
        f"today's run is {run_state(ti, run_date)}. "
        f"Last successful run_date: {last or f'none in the last {LOOKBACK_DAYS} days'}"
    )


def count_runs(ti: Any, run_date: str, states: list[str]) -> int:
    logical_date = athens_time(run_date, DAILY_RUN_HOUR)
    return ti.get_dr_count(DAILY_DAG_ID, logical_dates=[logical_date], states=states)


def succeeded(ti: Any, run_date: str) -> bool:
    return count_runs(ti, run_date, ["success"]) > 0


def run_state(ti: Any, run_date: str) -> str:
    for state in ("running", "queued", "failed"):
        if count_runs(ti, run_date, [state]):
            return state
    return "missing"


def last_success(ti: Any, run_date: str) -> str | None:
    day = date.fromisoformat(run_date)
    for back in range(1, LOOKBACK_DAYS + 1):
        earlier = (day - timedelta(days=back)).isoformat()
        if succeeded(ti, earlier):
            return earlier
    return None
