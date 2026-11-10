from datetime import UTC, date, datetime

import psycopg
import pytest

from jobtracker_ingest.loader.models import Fingerprint
from jobtracker_ingest.loader.warehouse import MemoryWarehouse, conninfo_from_env


def test_conninfo_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_HOST", "postgres")
    monkeypatch.setenv("WAREHOUSE_DB", "warehouse")
    monkeypatch.setenv("LOADER_PASSWORD", "secret")
    monkeypatch.delenv("POSTGRES_PORT", raising=False)
    monkeypatch.delenv("LOADER_USER", raising=False)

    params = psycopg.conninfo.conninfo_to_dict(conninfo_from_env())

    assert params["host"] == "postgres"
    assert params["port"] == "5432"
    assert params["dbname"] == "warehouse"
    assert params["user"] == "loader"
    assert params["password"] == "secret"


def test_memory_detail_keys_in_use() -> None:
    warehouse = MemoryWarehouse()
    with warehouse.transaction() as tx:
        tx.upsert_fingerprints(
            [
                Fingerprint(
                    source="greenhouse",
                    source_job_id=job_id,
                    listing_fingerprint="fp",
                    account=None,
                    url=f"https://example.org/jobs/{job_id}",
                    last_seen_run_date=seen,
                    detail_object_key=f"raw/greenhouse/{job_id}.json.gz",
                    detail_url=f"https://example.org/jobs/{job_id}",
                    detail_content_type="application/json",
                    detail_fetched_at=datetime(2026, 10, 1, tzinfo=UTC),
                    content_hash="abc",
                )
                for job_id, seen in [("1", date(2026, 10, 1)), ("2", date(2026, 11, 1))]
            ]
        )
    assert warehouse.detail_keys_in_use(date(2026, 10, 2)) == {"raw/greenhouse/2.json.gz"}
