"""run_date is the Europe/Athens calendar date of the run.

Every task gets it as a rendered template argument, worked out from the
time the run was due, never from the data interval.
"""

from datetime import UTC, date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

TIMEZONE = "Europe/Athens"

# Needs athens_date in the DAG's user_defined_macros
RUN_DATE = "{{ athens_date(dag_run.run_after) }}"


def athens_date(when: datetime) -> str:
    return when.astimezone(ZoneInfo(TIMEZONE)).date().isoformat()


def run_date_from_context(context: Any) -> str:
    """The same run_date the tasks got, for use outside templates."""
    return athens_date(context["dag_run"].run_after)


def athens_time(run_date: str, hour: int) -> datetime:
    """The moment it is hour:00 in Athens on run_date, in UTC."""
    local = datetime.combine(date.fromisoformat(run_date), time(hour), ZoneInfo(TIMEZONE))
    return local.astimezone(UTC)
