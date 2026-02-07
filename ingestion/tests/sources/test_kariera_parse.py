"""Fixture tests for KarieraAdapter.parse.

Each detail-*.html in fixtures/kariera has the expected parse output in
expected/ under the same name. The pages copy the structure of real
kariera.gr pages, with the JSON-LD and the Next.js flight data where the
site puts them, but the companies and texts are made up.
"""

import json
import re
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from jobtracker_ingest.core.models import RawPayload
from jobtracker_ingest.sources.kariera import (
    KarieraAdapter,
    ParseError,
    address_text,
    flight_text,
    job_records,
    json_ld,
    salary_text,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "kariera"
DETAILS = sorted(FIXTURES.glob("detail-*.html"))


def payload_for(path: Path) -> RawPayload:
    job_id = re.search(r"it-jobs/(\d+)\"", path.read_text(encoding="utf-8"))
    assert job_id, path.name
    return make_payload(job_id.group(1), path.read_bytes())


def make_payload(job_id: str, content: bytes) -> RawPayload:
    return RawPayload(
        source="kariera",
        source_job_id=job_id,
        url=f"https://www.kariera.gr/jobs/it-jobs/{job_id}",
        run_date=date(2026, 10, 25),
        fetched_at=datetime(2026, 10, 25, 3, 0, 12, tzinfo=UTC),
        content=content,
        content_type="text/html; charset=utf-8",
        account="it-jobs",
        object_key=f"raw/kariera/2026-10-25/run/detail/{job_id}.html.gz",
    )


def soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def test_every_detail_has_an_expected_file() -> None:
    assert len(DETAILS) >= 10
    for path in DETAILS:
        assert (FIXTURES / "expected" / f"{path.stem}.json").exists(), path.name


@pytest.mark.parametrize("path", DETAILS, ids=lambda p: p.stem)
def test_parse_matches_expected(path: Path) -> None:
    posting = KarieraAdapter().parse(payload_for(path))
    expected = json.loads((FIXTURES / "expected" / f"{path.stem}.json").read_text("utf-8"))
    assert posting.model_dump(mode="json") == expected


@pytest.mark.parametrize("path", DETAILS, ids=lambda p: p.stem)
def test_parse_is_repeatable(path: Path) -> None:
    adapter = KarieraAdapter()
    assert adapter.parse(payload_for(path)) == adapter.parse(payload_for(path))


def test_a_page_without_a_job_posting_is_an_error() -> None:
    page = b'<html><script type="application/ld+json">{"@type":"ItemList"}</script></html>'
    with pytest.raises(ParseError, match="no JobPosting JSON-LD"):
        KarieraAdapter().parse(make_payload("1", page))


def test_a_bare_job_posting_gives_mostly_nulls() -> None:
    page = b'<script type="application/ld+json">{"@type":"JobPosting"}</script>'
    posting = KarieraAdapter().parse(make_payload("1", page))
    assert posting.url == "https://www.kariera.gr/jobs/it-jobs/1"
    assert posting.title_raw is None
    assert posting.description_raw is None
    assert posting.workplace_raw is None


def test_json_ld_skips_broken_scripts_and_reads_lists() -> None:
    page = soup(
        '<script type="application/ld+json">{not json</script>'
        '<script type="application/ld+json">[{"@type":"BreadcrumbList"},'
        '{"@type":["JobPosting","Thing"],"title":"Found"}]</script>'
    )
    assert json_ld(page, "JobPosting") == {"@type": ["JobPosting", "Thing"], "title": "Found"}
    assert json_ld(page, "ItemList") is None


def test_flight_text_skips_chunks_it_cannot_read() -> None:
    page = soup(
        '<script>self.__next_f.push([1,"a"])</script>'
        '<script>self.__next_f.push([1,"broken \\x"])</script>'
        '<script>self.__next_f.push([1,"b"]);</script>'
        "<script>console.log(1)</script>"
        '<script src="/chunk.js"></script>'
    )
    assert flight_text(page) == "ab"


def test_job_records_keeps_the_first_and_skips_broken_ones() -> None:
    flight = '{"id":1,"title":"A"} {"id":1,"title":"B"} {"id":2,"title": oops'
    assert job_records(flight) == {"1": {"id": 1, "title": "A"}}


@pytest.mark.parametrize(
    ("address", "text"),
    [
        ("  Athens  ", "Athens"),
        ({"city": "Αθήνα", "region": "Αττική", "country": "Ελλάδα"}, "Αθήνα, Αττική, Ελλάδα"),
        ({"addressLocality": " ", "addressCountry": None}, None),
        (["not", "an", "address"], None),
        (None, None),
    ],
)
def test_address_text(address: object, text: str | None) -> None:
    assert address_text(address) == text


@pytest.mark.parametrize(
    ("salary", "text"),
    [
        (
            {"currency": "EUR", "value": {"minValue": 1800, "maxValue": 1800, "unitText": "MONTH"}},
            "1800 EUR per month",
        ),
        ({"currency": "EUR", "value": 2000}, "2000 EUR"),
        ({"value": {"minValue": 50, "unitText": "SHIFT"}}, "50 per shift"),
        (
            [
                {"currency": "EUR", "value": {"value": 1500, "unitText": "MONTH"}},
                {"currency": "EUR", "value": {"value": 300, "unitText": "WEEK"}},
            ],
            "1500 EUR per month; 300 EUR per week",
        ),
        ({"currency": "EUR", "value": {"minValue": None, "unitText": "MONTH"}}, None),
        ({"currency": "EUR", "value": True}, None),
        (None, None),
    ],
)
def test_salary_text(salary: object, text: str | None) -> None:
    assert salary_text(salary) == text
