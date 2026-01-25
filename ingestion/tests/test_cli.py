from collections.abc import Iterator
from pathlib import Path

import pytest
import responses
import structlog

from jobtracker_ingest import cli
from jobtracker_ingest.core.ratelimit import HostRateLimiter
from jobtracker_ingest.core.storage import MemoryObjectStore

FIXTURES = Path(__file__).parent / "fixtures" / "greenhouse"
API = "https://boards-api.greenhouse.io/v1/boards"
BOARD = f"{API}/acropolisanalytics/jobs"
JSON = "application/json; charset=utf-8"

REGISTRY = f"""
sources:
  - name: greenhouse
    kind: ats_api
    enabled: {{enabled}}
    base_url: {API}
    accounts: [acropolisanalytics]
    terms_reviewed_on: 2026-10-25
    robots_reviewed_on: 2026-10-25
"""


@pytest.fixture(autouse=True)
def reset_logging() -> Iterator[None]:
    # main() configures structlog globally, so undo it for the other tests
    yield
    structlog.reset_defaults()


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> MemoryObjectStore:
    memory = MemoryObjectStore()
    monkeypatch.setattr(cli.S3ObjectStore, "from_env", lambda: memory)
    monkeypatch.setenv("SCRAPER_CONTACT_EMAIL", "maintainer@example.org")
    monkeypatch.setenv("SCRAPER_REPO_URL", "https://github.com/example/jobs")
    # No real waiting between requests in tests
    monkeypatch.setattr(HostRateLimiter, "_wait_turn", lambda self, host: 0.0)
    return memory


@pytest.fixture
def rsps() -> Iterator[responses.RequestsMock]:
    with responses.RequestsMock() as mock:
        mock.get("https://boards-api.greenhouse.io/robots.txt", status=404)
        yield mock


def registry(tmp_path: Path, enabled: bool) -> str:
    path = tmp_path / "sources.yml"
    path.write_text(REGISTRY.format(enabled=str(enabled).lower()))
    return str(path)


def extract(tmp_path: Path, enabled: bool = True) -> int:
    path = registry(tmp_path, enabled)
    return cli.main(
        ["extract", "--source", "greenhouse", "--run-date", "2026-10-25", "--registry", path]
    )


def test_disabled_source_is_refused(tmp_path: Path, store: MemoryObjectStore) -> None:
    assert extract(tmp_path, enabled=False) == 1
    assert store.objects == {}


def test_extract_writes_a_run(
    tmp_path: Path, store: MemoryObjectStore, rsps: responses.RequestsMock
) -> None:
    rsps.get(BOARD, body=(FIXTURES / "listing-board.json").read_bytes(), content_type=JSON)
    for job_id, name in [
        ("4012345008", "detail-senior-data-engineer.json"),
        ("4012345009", "detail-backend-greek.json"),
        ("4012345010", "detail-remote-flags.json"),
    ]:
        rsps.get(
            f"{BOARD}/{job_id}?pay_transparency=true",
            body=(FIXTURES / name).read_bytes(),
            content_type=JSON,
        )

    assert extract(tmp_path) == 0
    manifests = [k for k in store.objects if k.endswith("/_manifest.json")]
    assert len(manifests) == 1
    assert manifests[0].startswith("raw/greenhouse/2026-10-25/")


def test_a_failed_extract_returns_1(
    tmp_path: Path, store: MemoryObjectStore, rsps: responses.RequestsMock
) -> None:
    rsps.get(BOARD, body=(FIXTURES / "listing-empty.json").read_bytes(), content_type=JSON)
    assert extract(tmp_path) == cli.EXIT_FAILED == 1
    assert not any(k.endswith("/_manifest.json") for k in store.objects)


def test_a_blocked_extract_returns_3(
    tmp_path: Path, store: MemoryObjectStore, rsps: responses.RequestsMock
) -> None:
    rsps.get(BOARD, status=403)
    assert extract(tmp_path) == cli.EXIT_BLOCKED == 3
    assert not any(k.endswith("/_manifest.json") for k in store.objects)
