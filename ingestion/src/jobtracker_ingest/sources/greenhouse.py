"""Greenhouse job boards, through the public Job Board API.

Listing: GET <base_url>/<board>/jobs returns every open job on one board,
so each board in the registry is one listing page.
Detail:  GET <base_url>/<board>/jobs/<id>?pay_transparency=true

These endpoints need no login or API key. Greenhouse has no structured
workplace type, so it is read from a custom metadata field when a company
sets one, and dbt falls back to the text otherwise.
"""

import hashlib
import html
import json
from collections.abc import Iterator
from typing import Any, ClassVar

from bs4 import BeautifulSoup

from jobtracker_ingest.core.http import DisallowedByRobots, HttpStatusError
from jobtracker_ingest.core.models import ListingRef, RawPayload, RawPosting
from jobtracker_ingest.sources.base import RunContext

# Metadata field names that companies use for these values, lowercased
WORKPLACE_FIELDS = ("workplace type", "workplace", "work mode", "location type", "remote")
EMPLOYMENT_FIELDS = ("employment type", "job type", "contract type")

# Tags that start a new line in the plain text description
BLOCK_TAGS = ("p", "div", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "table")


class GreenhouseAdapter:
    name: ClassVar[str] = "greenhouse"
    PARSER_VERSION: ClassVar[str] = "1"

    def list_postings(self, ctx: RunContext) -> Iterator[ListingRef]:
        base = ctx.config.base_url.rstrip("/")
        for board in ctx.config.accounts:
            url = f"{base}/{board}/jobs"
            try:
                result = ctx.client.get(url)
            except (HttpStatusError, DisallowedByRobots) as err:
                # Usually a 404 for a board that was closed or renamed.
                # Skip it and keep going with the other boards.
                ctx.log.warning("board_skipped", board=board, url=url, error=str(err))
                ctx.listings.record_error(url, str(err))
                continue

            ctx.listings.save_listing_page(result, account=board)
            for job in json.loads(result.content).get("jobs", []):
                job_id = str(job["id"])
                yield ListingRef(
                    source_job_id=job_id,
                    url=job["absolute_url"],
                    listing_fingerprint=listing_fingerprint(job),
                    account=board,
                    detail_url=f"{base}/{board}/jobs/{job_id}?pay_transparency=true",
                )

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
        job = json.loads(payload.content)
        metadata = metadata_values(job.get("metadata"))
        return RawPosting(
            source=payload.source,
            source_job_id=payload.source_job_id,
            run_date=payload.run_date,
            scraped_at=payload.fetched_at,
            url=clean(job.get("absolute_url")) or payload.url,
            title_raw=clean(job.get("title")),
            # Older boards leave out company_name. The board token is the
            # next best thing and company_aliases.csv can map it.
            company_raw=clean(job.get("company_name")) or payload.account,
            location_raw=clean((job.get("location") or {}).get("name")),
            workplace_raw=first_field(metadata, WORKPLACE_FIELDS),
            employment_type_raw=first_field(metadata, EMPLOYMENT_FIELDS),
            salary_raw=salary_text(job.get("pay_input_ranges")),
            department_raw=names(job.get("departments")),
            description_raw=html_to_text(job.get("content")),
            posted_at_raw=clean(job.get("first_published")),
            object_key=payload.object_key,
            parser_version=self.PARSER_VERSION,
        )


def listing_fingerprint(job: dict[str, Any]) -> str:
    updated_at = clean(job.get("updated_at"))
    if updated_at:
        return updated_at
    card = json.dumps(job, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(card.encode("utf-8")).hexdigest()


def clean(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return value.strip() or None


def metadata_values(metadata: Any) -> dict[str, str]:
    """Custom fields as {lowercased name: value as text}."""
    values: dict[str, str] = {}
    for field in metadata or []:
        name = clean(field.get("name"))
        value = field.get("value")
        if not name:
            continue
        if isinstance(value, bool):
            # A yes/no field only makes sense next to its name, as in "Remote: Yes"
            text = f"{name}: {'Yes' if value else 'No'}"
        elif isinstance(value, list):
            text = ", ".join(str(v).strip() for v in value if str(v).strip())
        elif isinstance(value, str | int | float):
            text = str(value).strip()
        else:
            continue
        if text:
            values.setdefault(name.lower(), text)
    return values


def first_field(metadata: dict[str, str], names: tuple[str, ...]) -> str | None:
    for name in names:
        if name in metadata:
            return metadata[name]
    return None


def names(items: Any) -> str | None:
    found = [clean(item.get("name")) for item in items or []]
    return ", ".join(n for n in found if n) or None


def salary_text(ranges: Any) -> str | None:
    """Pay ranges as text, for example "Base salary: 40000 - 60000 EUR".

    Greenhouse gives amounts in cents and no period, so dbt decides whether
    a range is monthly or annual.
    """
    parts = []
    for pay in ranges or []:
        amounts = [cents_to_text(pay.get(k)) for k in ("min_cents", "max_cents")]
        found = [a for a in amounts if a]
        if not found:
            continue
        # A range with the same min and max is shown as one amount
        text = " - ".join(dict.fromkeys(found))
        currency = clean(pay.get("currency_type"))
        if currency:
            text = f"{text} {currency}"
        title = clean(pay.get("title"))
        if title:
            text = f"{title}: {text}"
        parts.append(text)
    return "; ".join(parts) or None


def cents_to_text(cents: Any) -> str | None:
    if isinstance(cents, bool) or not isinstance(cents, int | float):
        return None
    whole, rest = divmod(round(cents), 100)
    return str(whole) if rest == 0 else f"{whole}.{rest:02d}"


def html_to_text(content: Any) -> str | None:
    """The job description as plain text, one block per line.

    Greenhouse sends the description HTML escaped, so it is unescaped
    first and then stripped of tags.
    """
    if not isinstance(content, str):
        return None
    soup = BeautifulSoup(html.unescape(content), "html.parser")
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for tag in soup.find_all(BLOCK_TAGS):
        tag.insert_before("\n")
        tag.insert_after("\n")
    lines = (" ".join(line.split()) for line in soup.get_text().splitlines())
    return "\n".join(line for line in lines if line) or None
