"""Workable careers pages, through the public account widget endpoint.

Listing: GET <base_url>/<account>?details=true returns the company name and
every public job of one account, descriptions included, so each account in
the registry is one listing page.
Detail:  none. The listing already holds everything, so the payload of each
posting is its job from that response (plus the company name) and
fetch_detail makes no request. This keeps us at one request per company.

A job open in several places is listed once per place with the same
shortcode, so the repeats are folded into one job that keeps every place.

The endpoint is documented in Workable's developer docs under public
endpoints. It needs no login or API key. We do not use the undocumented
apply.workable.com API or the hosted careers pages.
"""

import hashlib
import json
from collections.abc import Iterator
from typing import Any, ClassVar

from jobtracker_ingest.core.http import DisallowedByRobots, HttpStatusError
from jobtracker_ingest.core.models import ListingRef, RawPayload, RawPosting
from jobtracker_ingest.sources.base import ParseError, RunContext
from jobtracker_ingest.sources.text import clean, markup_to_text

PAYLOAD_CONTENT_TYPE = "application/json; charset=utf-8"
# Workable writes on_site, which the work mode seed reads as "on site"
WORKPLACE_TYPES = {"on_site": "on site"}
# Place fields, most specific first
PLACE_FIELDS = ("city", "state", "country")
# Our key on a job for the places of its repeated listings
OTHER_PLACES = "other_places"


class WorkableAdapter:
    name: ClassVar[str] = "workable"
    PARSER_VERSION: ClassVar[str] = "3"

    def __init__(self) -> None:
        # The payload of every posting listed in this run, by id, which
        # fetch_detail hands out without a request
        self._jobs: dict[str, RawPayload] = {}

    def list_postings(self, ctx: RunContext) -> Iterator[ListingRef]:
        base = ctx.config.base_url.rstrip("/")
        for account in ctx.config.accounts:
            url = f"{base}/{account}?details=true"
            try:
                result = ctx.client.get(url)
            except (HttpStatusError, DisallowedByRobots) as err:
                # Usually a 404 for an account that was closed or renamed.
                # Skip it and keep going with the other accounts.
                ctx.log.warning("account_skipped", account=account, url=url, error=str(err))
                ctx.listings.record_error(url, str(err))
                continue

            ctx.listings.save_listing_page(result, account=account)
            board = json.loads(result.content)
            company = clean(board.get("name")) or account
            jobs: dict[str, dict[str, Any]] = {}
            for job in board.get("jobs") or []:
                job_id = clean(job.get("shortcode"))
                if job_id is None or clean(job.get("url")) is None:
                    ctx.log.warning("listing_item_skipped", account=account, job=job.get("title"))
                    continue
                if job_id in jobs:
                    add_place(jobs[job_id], job)
                else:
                    jobs[job_id] = job

            for job_id, job in jobs.items():
                job_url = job["url"].strip()
                # A job listed by two accounts keeps the first, as extract does
                self._jobs.setdefault(
                    job_id,
                    RawPayload(
                        source=self.name,
                        source_job_id=job_id,
                        url=job_url,
                        run_date=ctx.run_date,
                        fetched_at=result.fetched_at,
                        content=job_bytes(company, job),
                        content_type=PAYLOAD_CONTENT_TYPE,
                        account=account,
                    ),
                )
                yield ListingRef(
                    source_job_id=job_id,
                    url=job_url,
                    listing_fingerprint=listing_fingerprint(job),
                    account=account,
                )

    def fetch_detail(self, ref: ListingRef, ctx: RunContext) -> RawPayload:
        """The job as listed. No request, the listing has the whole posting."""
        try:
            return self._jobs[ref.source_job_id]
        except KeyError:
            raise LookupError(f"{ref.source_job_id} was not listed in this run") from None

    def parse(self, payload: RawPayload) -> RawPosting:
        where = payload.object_key or payload.url
        try:
            record = json.loads(payload.content)
            job = record["job"]
        except (ValueError, KeyError, TypeError) as err:
            raise ParseError(f"not a Workable job: {where}") from err
        if not isinstance(job, dict):
            raise ParseError(f"not a Workable job: {where}")

        return RawPosting(
            source=payload.source,
            source_job_id=payload.source_job_id,
            run_date=payload.run_date,
            scraped_at=payload.fetched_at,
            url=clean(job.get("url")) or payload.url,
            title_raw=clean(job.get("title")),
            # The board token is the fallback, and company_aliases.csv can map it
            company_raw=clean(record.get("company")) or payload.account,
            location_raw=location_text(job),
            workplace_raw=workplace_text(job),
            employment_type_raw=clean(job.get("employment_type")),
            # The public endpoint has no pay information
            salary_raw=None,
            department_raw=department_text(job),
            description_raw=markup_to_text(job["description"])
            if isinstance(job.get("description"), str)
            else None,
            posted_at_raw=posted_text(job),
            object_key=payload.object_key,
            parser_version=self.PARSER_VERSION,
        )


def job_bytes(company: str, job: dict[str, Any]) -> bytes:
    record = {"company": company, "job": job}
    return json.dumps(record, ensure_ascii=False, sort_keys=True).encode("utf-8")


def listing_fingerprint(job: dict[str, Any]) -> str:
    """Hash of the whole job, since the endpoint has no updated_at."""
    card = json.dumps(job, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(card.encode("utf-8")).hexdigest()


def add_place(job: dict[str, Any], repeat: dict[str, Any]) -> None:
    """Keep the place of a repeated listing on the job it repeats."""
    place = {field: repeat.get(field) for field in PLACE_FIELDS}
    job.setdefault(OTHER_PLACES, []).append(place)


def place_text(place: dict[str, Any]) -> str | None:
    parts = [clean(place.get(field)) for field in PLACE_FIELDS]
    return ", ".join(dict.fromkeys(p for p in parts if p)) or None


def location_text(job: dict[str, Any]) -> str | None:
    """Every place of the job, in listing order, separated by semicolons."""
    places = [place_text(place) for place in [job, *job.get(OTHER_PLACES, [])]]
    return "; ".join(dict.fromkeys(p for p in places if p)) or None


def workplace_text(job: dict[str, Any]) -> str | None:
    """remote, hybrid or on site, from the structured fields only."""
    kind = clean(job.get("workplace_type"))
    if kind:
        return WORKPLACE_TYPES.get(kind.lower(), kind.replace("_", " "))
    # The older flag only says yes, so a false value tells us nothing
    if job.get("telecommuting") is True:
        return "remote"
    return None


def posted_text(job: dict[str, Any]) -> str | None:
    """The earlier of published_on and created_at.

    Republishing a job moves published_on to that day, while created_at keeps
    the day the job was made. One company republished every job on the same
    day, so published_on alone made old jobs look new. Both are ISO dates, so
    the earlier one is the smaller text.
    """
    dates = [clean(job.get(field)) for field in ("published_on", "created_at")]
    return min((d for d in dates if d), default=None)


def department_text(job: dict[str, Any]) -> str | None:
    department = job.get("department")
    # Workable sends a list on some accounts and a string on others
    if isinstance(department, list):
        found = [clean(d) for d in department]
        return ", ".join(d for d in found if d) or None
    return clean(department)
