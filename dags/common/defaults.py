"""Settings shared by every DAG.

run_date is the Europe/Athens calendar date of the run. Every task gets it
as a rendered template argument, worked out from the time the run was due,
never from the data interval.
"""

import os
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

TIMEZONE = "Europe/Athens"

# Caps how many sources are extracted at the same time
HTTP_POOL = "http_sources"

# config/sources.yml in the repo. The Airflow image sets JOBTRACKER_SOURCES,
# the same variable the ingestion CLI reads, so both see the same registry.
REPO_REGISTRY = Path(__file__).resolve().parents[2] / "config" / "sources.yml"

# Needs athens_date in the DAG's user_defined_macros
RUN_DATE = "{{ athens_date(dag_run.run_after) }}"


def athens_date(when: datetime) -> str:
    return when.astimezone(ZoneInfo(TIMEZONE)).date().isoformat()


def run_date_from_context(context: Any) -> str:
    """The same run_date the tasks got, for use outside templates."""
    return athens_date(context["dag_run"].run_after)


def enabled_sources(path: Path | None = None) -> list[str]:
    """Names of the enabled sources in the registry, in registry order.

    The ingestion CLI validates the registry when a task runs. This only
    reads it, because the DAG runs on Airflow's Python, not the ingestion
    venv.
    """
    path = path or Path(os.environ.get("JOBTRACKER_SOURCES", REPO_REGISTRY))
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return [s["name"] for s in data.get("sources") or [] if s.get("enabled")]
