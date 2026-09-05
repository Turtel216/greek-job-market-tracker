"""Settings shared by every DAG."""

import os
from datetime import timedelta
from pathlib import Path
from typing import Any

import yaml

from common import alerts

# Caps how many sources are extracted at the same time
HTTP_POOL = "http_sources"

# jobtracker_daily runs at this hour, Europe/Athens. The watchdog looks for
# the run scheduled then.
DAILY_RUN_HOUR = 6

# config/sources.yml in the repo. The Airflow image sets JOBTRACKER_SOURCES,
# the same variable the ingestion CLI reads, so both see the same registry.
REPO_REGISTRY = Path(__file__).resolve().parents[2] / "config" / "sources.yml"

DEFAULT_ARGS: dict[str, Any] = {
    "owner": "jobtracker",
    "retries": 0,
    # A hung task must not hold the run until the 10:00 watchdog
    "execution_timeout": timedelta(hours=2),
    "on_failure_callback": alerts.task_failed,
    "on_retry_callback": alerts.task_retried,
}


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
