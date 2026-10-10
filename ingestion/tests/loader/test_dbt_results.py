"""Reading dbt artifacts into ops.dbt_runs rows."""

import shutil
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

import pytest

from jobtracker_ingest.loader.dbt_results import load_dbt_results, node_name, read_dbt_results
from jobtracker_ingest.loader.warehouse import MemoryWarehouse

FIXTURES = Path(__file__).parents[1] / "fixtures" / "dbt"
RUN_DATE = date(2026, 10, 28)
BUILD = UUID("be727a38-f858-4e1b-b8fc-15744ff62c6d")
FRESHNESS = UUID("3f1c2a9e-7b4d-4e5f-9a8b-1c2d3e4f5a6b")


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    shutil.copytree(FIXTURES, tmp_path, dirs_exist_ok=True)
    # A step that did not run leaves an empty folder, or none at all
    (tmp_path / "snapshots").mkdir()
    return tmp_path


@pytest.mark.parametrize(
    ("unique_id", "name"),
    [
        ("test.jobtracker.not_null_dim_jobs_job_id.5d3f0a1b2c", "not_null_dim_jobs_job_id"),
        ("source.jobtracker.raw.raw_postings", "raw.raw_postings"),
        ("model.jobtracker.dim_jobs", "dim_jobs"),
        ("seed.jobtracker.skills", "skills"),
        ("unit_test.jobtracker.stg_postings.salary_ranges", "salary_ranges"),
    ],
)
def test_node_name(unique_id: str, name: str) -> None:
    assert node_name(unique_id) == name


def test_reads_every_step_with_an_artifact(folder: Path) -> None:
    rows = read_dbt_results(folder, RUN_DATE)
    assert {row.step for row in rows} == {"freshness", "staging"}
    assert len(rows) == 8
    assert {row.run_date for row in rows} == {RUN_DATE}


def test_build_results(folder: Path) -> None:
    rows = {
        row.unique_id: row for row in read_dbt_results(folder, RUN_DATE) if row.step == "staging"
    }
    assert {row.invocation_id for row in rows.values()} == {BUILD}
    assert {row.generated_at for row in rows.values()} == {
        datetime(2026, 10, 28, 16, 50, 57, 230326, tzinfo=UTC)
    }

    seed = rows["seed.jobtracker.company_placeholders"]
    assert (seed.resource_type, seed.name, seed.status) == (
        "seed",
        "company_placeholders",
        "success",
    )
    assert seed.rows_affected == 6
    assert seed.failures is None

    failed = rows["test.jobtracker.accepted_values_stg_postings_seniority.9d2c4e7f1a"]
    assert failed.resource_type == "test"
    assert failed.name == "accepted_values_stg_postings_seniority"
    assert (failed.status, failed.failures) == ("fail", 3)
    assert failed.message == "Got 3 results, configured to fail if != 0"


def test_no_row_count_is_null(folder: Path) -> None:
    # Postgres reports -1 rows for create view
    rows = {row.unique_id: row for row in read_dbt_results(folder, RUN_DATE)}
    assert rows["model.jobtracker.stg_raw_postings"].rows_affected is None


def test_freshness_results(folder: Path) -> None:
    rows = {
        row.unique_id: row for row in read_dbt_results(folder, RUN_DATE) if row.step == "freshness"
    }
    assert {row.invocation_id for row in rows.values()} == {FRESHNESS}

    raw = rows["source.jobtracker.raw.raw_postings"]
    assert (raw.resource_type, raw.name, raw.status) == ("source", "raw.raw_postings", "warn")
    assert raw.execution_time == pytest.approx(0.0412)

    # A check that could not run has no timing, only an error
    broken = rows["source.jobtracker.ops.ingest_runs"]
    assert broken.status == "runtime error"
    assert broken.execution_time is None
    assert broken.message is not None
    assert "does not exist" in broken.message


def test_empty_step_folders_give_no_rows(tmp_path: Path) -> None:
    (tmp_path / "staging").mkdir()
    assert read_dbt_results(tmp_path, RUN_DATE) == []


def test_load_writes_every_row(folder: Path) -> None:
    warehouse = MemoryWarehouse()
    assert load_dbt_results(folder, RUN_DATE, warehouse) == 8
    assert len(warehouse.dbt_runs) == 8


def test_loading_again_leaves_the_same_rows(folder: Path) -> None:
    warehouse = MemoryWarehouse()
    load_dbt_results(folder, RUN_DATE, warehouse)
    first = dict(warehouse.dbt_runs)
    load_dbt_results(folder, RUN_DATE, warehouse)
    assert warehouse.dbt_runs == first
