from datetime import UTC, datetime
from pathlib import Path

import pytest

from common.defaults import athens_date, enabled_sources

REGISTRY = """
sources:
  - name: greenhouse
    enabled: true
  - name: kariera
    enabled: false
  - name: lever
    enabled: true
"""


@pytest.mark.parametrize(
    ("when", "expected"),
    [
        # 06:00 in Athens during summer time
        (datetime(2026, 10, 24, 3, 0, tzinfo=UTC), "2026-10-24"),
        # Still the 24th in UTC, already the 25th in Athens
        (datetime(2026, 10, 24, 22, 30, tzinfo=UTC), "2026-10-25"),
        # Summer time ends on 2026-10-25, Athens is UTC+2 after that
        (datetime(2026, 10, 26, 4, 0, tzinfo=UTC), "2026-10-26"),
        (datetime(2026, 12, 31, 22, 0, tzinfo=UTC), "2027-01-01"),
    ],
)
def test_athens_date(when: datetime, expected: str) -> None:
    assert athens_date(when) == expected


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
