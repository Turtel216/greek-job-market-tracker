"""Fixture tests for GreenhouseAdapter.parse.

Each detail-*.json in fixtures/greenhouse has the expected parse output in
expected/ under the same name. The payloads are shaped like the Job Board
API responses; descriptions are HTML escaped as Greenhouse sends them.
"""

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from jobtracker_ingest.core.models import RawPayload
from jobtracker_ingest.sources.greenhouse import GreenhouseAdapter

FIXTURES = Path(__file__).parents[1] / "fixtures" / "greenhouse"
DETAILS = sorted(FIXTURES.glob("detail-*.json"))


def payload_for(path: Path) -> RawPayload:
    job_id = str(json.loads(path.read_bytes())["id"])
    return RawPayload(
        source="greenhouse",
        source_job_id=job_id,
        url=f"https://job-boards.greenhouse.io/acropolisanalytics/jobs/{job_id}",
        run_date=date(2026, 10, 25),
        fetched_at=datetime(2026, 10, 25, 3, 0, 12, tzinfo=UTC),
        content=path.read_bytes(),
        content_type="application/json; charset=utf-8",
        account="acropolisanalytics",
        object_key=f"raw/greenhouse/2026-10-25/run/detail/{job_id}.json.gz",
    )


def test_every_detail_has_an_expected_file() -> None:
    assert DETAILS
    for path in DETAILS:
        assert (FIXTURES / "expected" / path.name).exists(), path.name


@pytest.mark.parametrize("path", DETAILS, ids=lambda p: p.stem)
def test_parse_matches_expected(path: Path) -> None:
    posting = GreenhouseAdapter().parse(payload_for(path))
    expected = json.loads((FIXTURES / "expected" / path.name).read_text(encoding="utf-8"))
    assert posting.model_dump(mode="json") == expected


@pytest.mark.parametrize("path", DETAILS, ids=lambda p: p.stem)
def test_parse_is_repeatable(path: Path) -> None:
    adapter = GreenhouseAdapter()
    assert adapter.parse(payload_for(path)) == adapter.parse(payload_for(path))
