"""The dbt steps of the daily DAG must cover the whole dbt project.

Each step builds one selector from transform/selectors.yml. Together they
must pick every model, seed, snapshot and data test exactly once, so a new
folder that no step builds, or a test that would run in two steps, fails
here. Unit tests are left out, since the DAG does not run them.
"""

import shutil
import subprocess
from collections import Counter
from pathlib import Path

import pytest
from conftest import DAGS, DBT_STEPS

PROJECT = DAGS.parent / "transform"
BUILT = ["model", "seed", "snapshot", "test"]

pytestmark = pytest.mark.skipif(shutil.which("dbt") is None, reason="needs dbt")


def dbt_ls(tmp_path: Path, *args: str) -> list[str]:
    """Names of the nodes dbt would pick. Needs no database."""
    types = [flag for t in BUILT for flag in ("--resource-type", t)]
    result = subprocess.run(
        [
            "dbt",
            "ls",
            "--quiet",
            "--project-dir",
            str(PROJECT),
            "--profiles-dir",
            str(PROJECT),
            # The repo is mounted read-only in the Airflow image
            "--target-path",
            str(tmp_path / "target"),
            "--log-path",
            str(tmp_path / "logs"),
            *types,
            *args,
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout.split()


def test_steps_pick_every_node_once(tmp_path: Path) -> None:
    everything = dbt_ls(tmp_path)
    picked = Counter(node for step in DBT_STEPS for node in dbt_ls(tmp_path, "--selector", step))

    assert everything
    assert sorted(picked) == sorted(everything)
    assert [node for node, count in picked.items() if count > 1] == []


def test_a_test_across_layers_runs_in_the_later_step(tmp_path: Path) -> None:
    # Reads int_posting_lifecycle and stg_postings
    test = (
        "jobtracker.intermediate."
        "relationships_int_posting_lifecycle_posting_key__posting_key__ref_stg_postings_"
    )
    assert test in dbt_ls(tmp_path, "--selector", "intermediate")
    assert test not in dbt_ls(tmp_path, "--selector", "staging")
