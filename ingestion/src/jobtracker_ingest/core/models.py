"""Typed records that cross boundaries between adapters, the extract runner
and the loader. See the Ingestion and Data model sections of the design doc.
"""

import hashlib
import json
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, computed_field


class Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ListingRef(Model):
    """One posting as seen on a listing page or the ATS listing endpoint."""

    source_job_id: str
    # Canonical posting URL, the page a person would open
    url: str
    # Changes when the posting changes, for example the API's updated_at
    listing_fingerprint: str
    # ATS board or company account the posting came from, if any
    account: str | None = None
    # Where the adapter fetches the detail from, when it is not url
    detail_url: str | None = None


class RawPayload(Model):
    """The bytes fetched for one posting, plus what parse needs to know
    about how and when they were fetched."""

    source: str
    source_job_id: str
    url: str
    run_date: date
    fetched_at: datetime
    content: bytes
    content_type: str
    account: str | None = None
    # Set once the payload is stored in the object store
    object_key: str | None = None


class RawPosting(Model):
    """One row of raw.raw_postings, before the loader adds load_id."""

    source: str
    source_job_id: str
    run_date: date
    scraped_at: datetime
    url: str
    title_raw: str | None
    company_raw: str | None
    location_raw: str | None
    workplace_raw: str | None
    employment_type_raw: str | None
    salary_raw: str | None
    department_raw: str | None
    description_raw: str | None
    posted_at_raw: str | None
    object_key: str | None
    parser_version: str

    @computed_field  # type: ignore[prop-decorator]
    @property
    def content_hash(self) -> str:
        """sha256 of the parsed fields, so it changes only when the posting does."""
        fields = self.model_dump(include=CONTENT_FIELDS)
        blob = json.dumps(fields, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# Fields that describe the posting itself, as opposed to the fetch
CONTENT_FIELDS = {
    "url",
    "title_raw",
    "company_raw",
    "location_raw",
    "workplace_raw",
    "employment_type_raw",
    "salary_raw",
    "department_raw",
    "description_raw",
    "posted_at_raw",
}
