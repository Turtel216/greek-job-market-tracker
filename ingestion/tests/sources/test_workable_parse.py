"""Fixture tests for WorkableAdapter.parse.

Each detail-*.json in fixtures/workable has the expected parse output in
expected/ under the same name. The payloads are jobs shaped like the public
account endpoint response, for a made up company.
"""

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from jobtracker_ingest.core.models import RawPayload
from jobtracker_ingest.sources.workable import ParseError, WorkableAdapter, posted_text

FIXTURES = Path(__file__).parents[1] / "fixtures" / "workable"
DETAILS = sorted(FIXTURES.glob("detail-*.json"))


def payload_for(path: Path, content: bytes | None = None) -> RawPayload:
    job = json.loads(path.read_bytes())["job"]
    return RawPayload(
        source="workable",
        source_job_id=job["shortcode"],
        url=job["url"],
        run_date=date(2026, 10, 25),
        fetched_at=datetime(2026, 10, 25, 3, 0, 12, tzinfo=UTC),
        content=content if content is not None else path.read_bytes(),
        content_type="application/json; charset=utf-8",
        account="acropolis",
        object_key=f"raw/workable/2026-10-25/run/detail/{job['shortcode']}.json.gz",
    )


def test_every_detail_has_an_expected_file() -> None:
    assert DETAILS
    for path in DETAILS:
        assert (FIXTURES / "expected" / path.name).exists(), path.name


@pytest.mark.parametrize("path", DETAILS, ids=lambda p: p.stem)
def test_parse_matches_expected(path: Path) -> None:
    posting = WorkableAdapter().parse(payload_for(path))
    expected = json.loads((FIXTURES / "expected" / path.name).read_text(encoding="utf-8"))
    assert posting.model_dump(mode="json") == expected


@pytest.mark.parametrize("path", DETAILS, ids=lambda p: p.stem)
def test_parse_is_repeatable(path: Path) -> None:
    adapter = WorkableAdapter()
    payload = payload_for(path)
    assert adapter.parse(payload) == adapter.parse(payload)


def test_company_falls_back_to_the_account() -> None:
    path = FIXTURES / "detail-sparse.json"
    record = json.loads(path.read_bytes())
    record["company"] = ""
    posting = WorkableAdapter().parse(payload_for(path, json.dumps(record).encode()))
    assert posting.company_raw == "acropolis"


@pytest.mark.parametrize(
    ("published_on", "created_at", "expected"),
    [
        # Republished long after it was made
        ("2026-11-02", "2025-09-17", "2025-09-17"),
        ("2026-10-20", "2026-10-20", "2026-10-20"),
        # Not seen in real payloads, but the earlier date still wins
        ("2026-10-18", "2026-10-19", "2026-10-18"),
        (None, "2026-10-19", "2026-10-19"),
        ("2026-10-20", "", "2026-10-20"),
        (None, None, None),
    ],
)
def test_posted_date_is_the_earlier_one(
    published_on: str | None, created_at: str | None, expected: str | None
) -> None:
    assert posted_text({"published_on": published_on, "created_at": created_at}) == expected


@pytest.mark.parametrize("content", [b"not json", b"[]", b'{"company": "x"}', b'{"job": 3}'])
def test_parse_rejects_other_payloads(content: bytes) -> None:
    with pytest.raises(ParseError):
        WorkableAdapter().parse(payload_for(FIXTURES / "detail-sparse.json", content))
