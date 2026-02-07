"""kariera.gr, a Greek job board, read from its public HTML pages.

Listing: GET <base_url>/<category>?page=<n>, 50 postings a page, starting
at page 1. A page past the end lists nothing. Each category in the
registry accounts, for example it-jobs, is listed on its own.
Detail:  GET <base_url>/<category>/<id>, the posting URL itself.

The pages are rendered on the server and carry the data twice, so nothing
has to be scraped out of the visible text:

- schema.org JSON-LD: an ItemList on listing pages and a JobPosting on
  detail pages. This is the main source, since it is a public format the
  site keeps stable for search engines.
- the Next.js flight data in the self.__next_f.push(...) scripts, which
  holds the site's own job record. It is used for what JSON-LD cannot say,
  which is partially or temporarily remote work, and as a fallback.

robots.txt disallows /api/, /_next/data/ and *.json, so the adapter only
ever reads the HTML pages and never the JSON endpoints behind them.
"""

import hashlib
import json
import re
from collections.abc import Iterator
from typing import Any, ClassVar

from bs4 import BeautifulSoup

from jobtracker_ingest.core.http import DisallowedByRobots, HttpStatusError
from jobtracker_ingest.core.models import ListingRef, RawPayload, RawPosting
from jobtracker_ingest.sources.base import RunContext
from jobtracker_ingest.sources.text import clean, markup_to_text

# One chunk of flight data, a JSON string inside a push call
FLIGHT_CHUNK = re.compile(r'\s*self\.__next_f\.push\(\[1,(".*")\]\)\s*;?\s*', re.S)
# The start of a job record in the flight data. Company objects also have
# an id, but they are followed by a name, not a title.
JOB_RECORD = re.compile(r'\{"id":(\d+),"title":')
JOB_ID_IN_URL = re.compile(r"/(\d+)/?$")

# Record fields that describe the posting, for the listing fingerprint.
# Logos and per company job counters change too often to be useful.
FINGERPRINT_FIELDS = (
    "title",
    "publishedAt",
    "expiresAt",
    "remote",
    "occupationType",
    "minSalaryRange",
    "maxSalaryRange",
    "payrollFrequency",
    "location",
)


class ParseError(ValueError):
    """The page does not look like a kariera job page."""


class KarieraAdapter:
    name: ClassVar[str] = "kariera"
    PARSER_VERSION: ClassVar[str] = "1"

    def list_postings(self, ctx: RunContext) -> Iterator[ListingRef]:
        base = ctx.config.base_url.rstrip("/")
        for category in ctx.config.accounts:
            yield from self._list_category(ctx, f"{base}/{category}", category)

    def _list_category(self, ctx: RunContext, url: str, category: str) -> Iterator[ListingRef]:
        seen: set[str] = set()
        for page in range(1, ctx.config.max_pages + 1):
            page_url = f"{url}?page={page}"
            try:
                result = ctx.client.get(page_url)
            except (HttpStatusError, DisallowedByRobots) as err:
                # Skip the rest of this category and keep going with the others
                ctx.log.warning("category_skipped", category=category, url=page_url, error=str(err))
                ctx.listings.record_error(page_url, str(err))
                return

            ctx.listings.save_listing_page(result, account=category)
            soup = BeautifulSoup(result.content, "html.parser")
            records = job_records(flight_text(soup))
            new = 0
            for item in item_list(soup):
                posting_url = clean(item.get("url"))
                job_id = job_id_from_url(posting_url)
                if posting_url is None or job_id is None:
                    ctx.log.warning("listing_item_skipped", category=category, item=item)
                    continue
                if job_id in seen:
                    continue
                seen.add(job_id)
                new += 1
                yield ListingRef(
                    source_job_id=job_id,
                    url=posting_url,
                    listing_fingerprint=listing_fingerprint(item, records.get(job_id)),
                    account=category,
                )
            # Past the last page, or the site ignored the page number
            if new == 0:
                return
        ctx.log.warning("max_pages_reached", category=category, max_pages=ctx.config.max_pages)

    def fetch_detail(self, ref: ListingRef, ctx: RunContext) -> RawPayload:
        result = ctx.client.get(ref.detail_url or ref.url)
        return RawPayload(
            source=self.name,
            source_job_id=ref.source_job_id,
            url=ref.url,
            run_date=ctx.run_date,
            fetched_at=result.fetched_at,
            content=result.content,
            content_type=result.content_type,
            account=ref.account,
        )

    def parse(self, payload: RawPayload) -> RawPosting:
        soup = BeautifulSoup(payload.content, "html.parser")
        posting = json_ld(soup, "JobPosting")
        if posting is None:
            raise ParseError(f"no JobPosting JSON-LD in {payload.object_key or payload.url}")
        record = job_records(flight_text(soup)).get(payload.source_job_id, {})
        description = posting.get("description")

        return RawPosting(
            source=payload.source,
            source_job_id=payload.source_job_id,
            run_date=payload.run_date,
            scraped_at=payload.fetched_at,
            url=clean(posting.get("url")) or payload.url,
            title_raw=clean(posting.get("title")) or clean(record.get("title")),
            company_raw=clean(dig(posting, "hiringOrganization", "name"))
            or clean(dig(record, "company", "name")),
            location_raw=places_text(posting.get("jobLocation"))
            or address_text(record.get("location")),
            # The record says DISABLED, PARTIALLY_REMOTE, TEMPORARILY_REMOTE
            # or FULLY_REMOTE. JSON-LD only marks fully remote jobs.
            workplace_raw=clean(record.get("remote")) or clean(posting.get("jobLocationType")),
            employment_type_raw=joined(posting.get("employmentType"))
            or clean(record.get("occupationType")),
            salary_raw=salary_text(posting.get("baseSalary")),
            department_raw=clean(posting.get("occupationalCategory"))
            or clean(record.get("category")),
            description_raw=markup_to_text(description) if isinstance(description, str) else None,
            posted_at_raw=clean(posting.get("datePosted")) or clean(record.get("publishedAt")),
            object_key=payload.object_key,
            parser_version=self.PARSER_VERSION,
        )


def job_id_from_url(url: str | None) -> str | None:
    match = JOB_ID_IN_URL.search(url or "")
    return match.group(1) if match else None


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def dig(value: Any, *keys: str) -> Any:
    """value[k1][k2]..., or None as soon as something is not a dict."""
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def json_ld(soup: BeautifulSoup, type_name: str) -> dict[str, Any] | None:
    """The first JSON-LD object of this schema.org type on the page."""
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except ValueError:
            continue
        for item in as_list(data):
            if isinstance(item, dict) and type_name in as_list(item.get("@type")):
                return item
    return None


def item_list(soup: BeautifulSoup) -> list[dict[str, Any]]:
    items = as_list(dig(json_ld(soup, "ItemList"), "itemListElement"))
    return [item for item in items if isinstance(item, dict)]


def flight_text(soup: BeautifulSoup) -> str:
    """Every flight data chunk on the page, joined back together.

    Next.js splits long rows across chunks, so a job record can start in
    one script and end in the next.
    """
    chunks: list[str] = []
    for script in soup.find_all("script"):
        match = FLIGHT_CHUNK.fullmatch(script.string or "")
        if not match:
            continue
        try:
            chunks.append(json.loads(match.group(1)))
        except ValueError:
            continue
    return "".join(chunks)


def job_records(flight: str) -> dict[str, dict[str, Any]]:
    """The site's job records by id. Listing pages repeat them, the first one wins."""
    decoder = json.JSONDecoder()
    records: dict[str, dict[str, Any]] = {}
    for match in JOB_RECORD.finditer(flight):
        job_id = match.group(1)
        if job_id in records:
            continue
        try:
            record, _ = decoder.raw_decode(flight, match.start())
        except ValueError:
            continue
        records[job_id] = record
    return records


def listing_fingerprint(item: dict[str, Any], record: dict[str, Any] | None) -> str:
    """A hash of the job record, or of the ItemList entry when there is none."""
    if record:
        card: dict[str, Any] = {key: record.get(key) for key in FINGERPRINT_FIELDS}
        card["company"] = dig(record, "company", "name")
    else:
        card = item
    blob = json.dumps(card, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def address_text(address: Any) -> str | None:
    """A JSON-LD PostalAddress or a record location, as "city, region, country"."""
    if isinstance(address, str):
        return clean(address)
    if not isinstance(address, dict):
        return None
    keys = ("addressLocality", "addressRegion", "addressCountry", "city", "region", "country")
    parts = [clean(address.get(key)) for key in keys]
    # The city and the region are sometimes the same, so drop repeats
    return ", ".join(dict.fromkeys(p for p in parts if p)) or None


def places_text(places: Any) -> str | None:
    found = [address_text(dig(place, "address")) for place in as_list(places)]
    return "; ".join(dict.fromkeys(f for f in found if f)) or None


def joined(value: Any) -> str | None:
    found = [clean(v) for v in as_list(value)]
    return ", ".join(f for f in found if f) or None


def salary_text(base_salary: Any) -> str | None:
    """A JSON-LD baseSalary as text, for example "2000 - 2500 EUR per month"."""
    parts = []
    for amount in as_list(base_salary):
        value = dig(amount, "value")
        if isinstance(value, dict):
            found = [number_text(value.get(k)) for k in ("minValue", "maxValue", "value")]
            unit = clean(value.get("unitText"))
        else:
            found, unit = [number_text(value)], None
        numbers = [n for n in found if n]
        if not numbers:
            continue
        # A range with the same min and max is shown as one amount
        text = " - ".join(dict.fromkeys(numbers))
        currency = clean(dig(amount, "currency"))
        if currency:
            text = f"{text} {currency}"
        # unitText is MONTH, YEAR and so on. "per month" is what the salary
        # rules in dbt look for.
        if unit:
            text = f"{text} per {unit.lower()}"
        parts.append(text)
    return "; ".join(parts) or None


def number_text(value: Any) -> str | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value)
