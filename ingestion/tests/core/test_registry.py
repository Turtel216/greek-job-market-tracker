from pathlib import Path

import pytest
from pydantic import ValidationError

from jobtracker_ingest.core.registry import REPO_REGISTRY, Registry, load_registry

GREENHOUSE = {
    "name": "greenhouse",
    "kind": "ats_api",
    "base_url": "https://boards-api.greenhouse.io/v1/boards",
    "accounts": ["example"],
}


def test_repo_registry_loads() -> None:
    registry = load_registry(REPO_REGISTRY)
    assert all(s.terms_reviewed_on and s.robots_reviewed_on for s in registry.enabled)


def test_load_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "sources.yml"
    path.write_text("sources:\n  - name: greenhouse\n    kind: ats_api\n    base_url: https://x\n")
    monkeypatch.setenv("JOBTRACKER_SOURCES", str(path))
    assert load_registry().get("greenhouse").base_url == "https://x"


def test_empty_file_has_no_sources(tmp_path: Path) -> None:
    path = tmp_path / "sources.yml"
    path.write_text("")
    assert load_registry(path).sources == []


def test_defaults() -> None:
    source = Registry.model_validate({"sources": [GREENHOUSE]}).get("greenhouse")
    assert not source.enabled
    assert source.min_delay_seconds == 3
    assert source.max_pages == 50


def test_enabled_needs_both_review_dates() -> None:
    entry = {**GREENHOUSE, "enabled": True, "terms_reviewed_on": "2026-11-01"}
    with pytest.raises(ValidationError, match="robots_reviewed_on is missing"):
        Registry.model_validate({"sources": [entry]})


def test_enabled_with_review_dates() -> None:
    entry = {
        **GREENHOUSE,
        "enabled": True,
        "terms_reviewed_on": "2026-11-01",
        "robots_reviewed_on": "2026-11-01",
    }
    registry = Registry.model_validate({"sources": [entry]})
    assert [s.name for s in registry.enabled] == ["greenhouse"]


def test_delay_below_the_floor_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Registry.model_validate({"sources": [{**GREENHOUSE, "min_delay_seconds": 1}]})


def test_duplicate_names_are_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate source names: greenhouse"):
        Registry.model_validate({"sources": [GREENHOUSE, GREENHOUSE]})


def test_unknown_source() -> None:
    with pytest.raises(KeyError):
        Registry.model_validate({"sources": []}).get("greenhouse")
