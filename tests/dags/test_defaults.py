from pathlib import Path

import pytest

from common.defaults import enabled_sources

REGISTRY = """
sources:
  - name: greenhouse
    enabled: true
  - name: kariera
    enabled: false
  - name: lever
    enabled: true
"""


def test_enabled_sources_in_registry_order(tmp_path: Path) -> None:
    path = tmp_path / "sources.yml"
    path.write_text(REGISTRY)
    assert enabled_sources(path) == ["greenhouse", "lever"]


def test_enabled_sources_reads_the_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "sources.yml"
    path.write_text("sources:\n")
    monkeypatch.setenv("JOBTRACKER_SOURCES", str(path))
    assert enabled_sources() == []


def test_repo_registry_loads() -> None:
    # The real registry must at least parse
    assert isinstance(enabled_sources(Path(__file__).parents[2] / "config" / "sources.yml"), list)
