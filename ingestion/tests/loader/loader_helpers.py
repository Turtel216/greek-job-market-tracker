"""Helpers that build stored extract runs from the Greenhouse fixtures."""

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from jobtracker_ingest.core.manifest import RunManifest
from jobtracker_ingest.core.models import RawPayload, RawPosting
from jobtracker_ingest.sources.greenhouse import GreenhouseAdapter

FIXTURES = Path(__file__).parents[1] / "fixtures" / "greenhouse"
JSON = "application/json; charset=utf-8"
DAY_1 = date(2026, 10, 25)
DAY_2 = date(2026, 10, 26)

# source_job_id to fixture file
DETAILS = {
    "4012345008": "detail-senior-data-engineer.json",
    "4012345009": "detail-backend-greek.json",
    "4012345010": "detail-remote-flags.json",
}


@dataclass(frozen=True)
class Posting:
    source_job_id: str
    # Raw detail bytes, or None when extract did not fetch the detail
    content: bytes | None
    listing_fingerprint: str = "fp-1"


def fixture_posting(job_id: str, fetch: bool = True, fingerprint: str = "fp-1") -> Posting:
    content = (FIXTURES / DETAILS[job_id]).read_bytes() if fetch else None
    return Posting(job_id, content, fingerprint)


def with_description(job_id: str, extra: str) -> Posting:
    """A fixture posting with text added to its description."""
    job = json.loads((FIXTURES / DETAILS[job_id]).read_bytes())
    job["content"] = f"{job['content']}&lt;p&gt;{extra}&lt;/p&gt;"
    return Posting(job_id, json.dumps(job).encode())


WriteRun = Callable[..., RunManifest]


class BrokenGreenhouse(GreenhouseAdapter):
    """A parser release that fails on every page."""

    PARSER_VERSION = "broken"

    def parse(self, payload: RawPayload) -> RawPosting:
        raise ValueError("the page changed")


class OldGreenhouse(GreenhouseAdapter):
    """The parser before a fix: it lost every title."""

    PARSER_VERSION = "old"

    def parse(self, payload: RawPayload) -> RawPosting:
        row = super().parse(payload)
        return row.model_copy(update={"title_raw": None, "parser_version": self.PARSER_VERSION})
