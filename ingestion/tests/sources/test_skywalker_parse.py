"""Fixture tests for SkywalkerAdapter.parse.

Each detail-*.xml in fixtures/skywalker is one feed item, the payload the
adapter stores for a posting, and has the expected parse output in
expected/ under the same name. The items copy the tags and values of the
real skywalker.gr feed, but the companies and texts are made up.
"""

import json
import re
import xml.etree.ElementTree as ET
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from jobtracker_ingest.core.models import RawPayload
from jobtracker_ingest.sources.base import ParseError
from jobtracker_ingest.sources.skywalker import SkywalkerAdapter, joined, values

FIXTURES = Path(__file__).parents[1] / "fixtures" / "skywalker"
DETAILS = sorted(FIXTURES.glob("detail-*.xml"))
IT = "01hdtm1ff0g1rdef228eftwthm"


def payload_for(path: Path) -> RawPayload:
    job_id = re.search(r"aggelia-ergasias/([0-9a-z]{26})/", path.read_text(encoding="utf-8"))
    assert job_id, path.name
    return make_payload(job_id.group(1), path.read_bytes())


def make_payload(job_id: str, content: bytes) -> RawPayload:
    return RawPayload(
        source="skywalker",
        source_job_id=job_id,
        url=f"https://www.skywalker.gr/el/aggelia-ergasias/{job_id}/posting",
        run_date=date(2026, 10, 29),
        fetched_at=datetime(2026, 10, 29, 4, 0, 5, tzinfo=UTC),
        content=content,
        content_type="application/xml; charset=utf-8",
        account=IT,
        object_key=f"raw/skywalker/2026-10-29/run/detail/{job_id}.xml.gz",
    )


def test_every_detail_has_an_expected_file() -> None:
    assert len(DETAILS) >= 10
    for path in DETAILS:
        assert (FIXTURES / "expected" / f"{path.stem}.json").exists(), path.name


@pytest.mark.parametrize("path", DETAILS, ids=lambda p: p.stem)
def test_parse_matches_expected(path: Path) -> None:
    posting = SkywalkerAdapter().parse(payload_for(path))
    expected = json.loads((FIXTURES / "expected" / f"{path.stem}.json").read_text("utf-8"))
    assert posting.model_dump(mode="json") == expected


@pytest.mark.parametrize("path", DETAILS, ids=lambda p: p.stem)
def test_parse_is_repeatable(path: Path) -> None:
    adapter = SkywalkerAdapter()
    assert adapter.parse(payload_for(path)) == adapter.parse(payload_for(path))


def test_the_prefecture_comes_before_a_municipality_named_like_another_city() -> None:
    # Dimos Irakleiou is a suburb of Athens; the prefecture says so first
    posting = SkywalkerAdapter().parse(payload_for(FIXTURES / "detail-many-municipalities.xml"))
    assert posting.location_raw is not None
    assert posting.location_raw.startswith("Νομός Αττικής, ")
    assert "Δήμος Ηρακλείου" in posting.location_raw


def test_a_page_that_is_not_xml_is_an_error() -> None:
    with pytest.raises(ParseError, match="not XML"):
        SkywalkerAdapter().parse(make_payload("1", b"<html><p>Just a moment...</html>"))


def test_a_whole_feed_is_not_an_item() -> None:
    feed = b'<?xml version="1.0"?><rss version="2.0"><channel><item/></channel></rss>'
    with pytest.raises(ParseError, match="no feed item in raw/skywalker"):
        SkywalkerAdapter().parse(make_payload("1", feed))


def test_a_bare_item_gives_mostly_nulls() -> None:
    posting = SkywalkerAdapter().parse(make_payload("1", b"<item><title> </title></item>"))
    assert posting.url == "https://www.skywalker.gr/el/aggelia-ergasias/1/posting"
    assert posting.title_raw is None
    assert posting.location_raw is None
    assert posting.description_raw is None
    assert posting.salary_raw is None


def test_values_keeps_tag_order_and_drops_repeats_and_blanks() -> None:
    item = ET.fromstring(
        "<item><city>B</city><state>A</state><state> </state><city>B</city><state>C</state>"
        "<state/></item>"
    )
    assert values(item, "state", "city") == ["A", "C", "B"]
    assert values(item, "country") == []
    assert joined(values(item, "state", "city")) == "A, C, B"
    assert joined([]) is None
