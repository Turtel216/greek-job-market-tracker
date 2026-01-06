"""The interface every source adapter implements.

The extract runner calls list_postings, then fetch_detail for each posting
that needs it, and stores every payload. The loader later calls parse on
the stored payloads, so parse must never touch the network.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from typing import Any, ClassVar, Protocol

from jobtracker_ingest.core.http import FetchResult, PoliteClient
from jobtracker_ingest.core.models import ListingRef, RawPayload, RawPosting
from jobtracker_ingest.core.registry import SourceConfig


class ListingSink(Protocol):
    """Where an adapter hands the listing pages it fetched."""

    def save_listing_page(self, result: FetchResult, account: str | None = None) -> str:
        """Store one listing page and return its object key."""
        ...

    def record_error(self, url: str, error: str) -> None:
        """Note a page that was skipped, for the run manifest."""
        ...


@dataclass(frozen=True)
class RunContext:
    config: SourceConfig
    run_date: date
    run_id: str
    client: PoliteClient
    listings: ListingSink
    log: Any


class SourceAdapter(Protocol):
    name: ClassVar[str]
    # Bump whenever parse output changes
    PARSER_VERSION: ClassVar[str]

    def list_postings(self, ctx: RunContext) -> Iterator[ListingRef]:
        """Yield one ListingRef per posting on the listing pages or API."""
        ...

    def fetch_detail(self, ref: ListingRef, ctx: RunContext) -> RawPayload:
        """Fetch the detail page or JSON for one posting."""
        ...

    def parse(self, payload: RawPayload) -> RawPosting:
        """Pure function: payload bytes in, typed record out. No network."""
        ...
