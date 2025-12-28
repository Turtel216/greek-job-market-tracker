from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from jobtracker_ingest.core.models import RawPosting


def posting(**changes: object) -> RawPosting:
    fields: dict[str, object] = {
        "source": "greenhouse",
        "source_job_id": "1",
        "run_date": date(2026, 10, 25),
        "scraped_at": datetime(2026, 10, 25, 4, 0, tzinfo=UTC),
        "url": "https://example.org/jobs/1",
        "title_raw": "Data Engineer",
        "company_raw": "Example",
        "location_raw": "Athens, Greece",
        "workplace_raw": None,
        "employment_type_raw": None,
        "salary_raw": None,
        "department_raw": None,
        "description_raw": "We are hiring.",
        "posted_at_raw": None,
        "object_key": "raw/greenhouse/2026-10-25/run/detail/1.json.gz",
        "parser_version": "1",
    }
    fields.update(changes)
    return RawPosting.model_validate(fields)


def test_content_hash_ignores_fetch_details() -> None:
    first = posting()
    later = posting(
        run_date=date(2026, 10, 26),
        scraped_at=datetime(2026, 10, 26, 4, 0, tzinfo=UTC),
        object_key="raw/greenhouse/2026-10-26/run/detail/1.json.gz",
        parser_version="2",
    )
    assert first.content_hash == later.content_hash


def test_content_hash_changes_with_the_posting() -> None:
    assert posting().content_hash != posting(title_raw="Senior Data Engineer").content_hash


def test_content_hash_follows_copies() -> None:
    scrubbed = posting().model_copy(update={"description_raw": "Scrubbed."})
    assert scrubbed.content_hash == posting(description_raw="Scrubbed.").content_hash


def test_content_hash_is_in_the_dump() -> None:
    assert posting().model_dump()["content_hash"] == posting().content_hash


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        posting(salary="1000")
