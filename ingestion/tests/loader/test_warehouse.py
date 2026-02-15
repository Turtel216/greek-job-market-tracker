import psycopg
import pytest

from jobtracker_ingest.loader.warehouse import conninfo_from_env


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
