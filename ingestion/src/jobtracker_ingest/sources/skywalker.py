"""skywalker.gr, a Greek job board, read from its public RSS feed.

Listing: GET <base_url>?category=<id>&page=<n>, 25 postings a page,
starting at page 1. A page past the end is a feed with no items. Each
category id in the registry accounts is listed on its own. The ids are
the ones the site's own search uses.
Detail:  none. The posting pages sit behind a Cloudflare challenge, which
we never try to pass, so the payload of each posting is its <item> from
the feed and fetch_detail makes no request.

The feed has the title, company, categories, work type, employment type,
place and posting date, but only the first 150 or so characters of the
description and no salary. Skills are matched on the title and that short
description only.

The standard library XML parser never loads external entities, and expat
2.4 or later, which Python 3.12 ships with, limits entity expansion, so it
is safe on feed data.
"""

import copy
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from typing import ClassVar

from jobtracker_ingest.core.http import DisallowedByRobots, HttpStatusError
from jobtracker_ingest.core.models import ListingRef, RawPayload, RawPosting
from jobtracker_ingest.sources.base import ParseError, RunContext, ServesListedPayloads
from jobtracker_ingest.sources.text import clean, tidy_lines

# Every feed title starts with "Available job position: " in Greek
TITLE_PREFIX = re.compile(r"^\s*Διαθέσιμη θέση εργασίας\s*:\s*")
# The posting id is the 26 character ULID in the posting URL
JOB_ID_IN_URL = re.compile(r"/aggelia-ergasias/([0-9a-z]{26})(?:/|$)")
ITEM_CONTENT_TYPE = "application/xml; charset=utf-8"
# The logo URL changes when a company uploads a new logo, which says
# nothing about the posting, so it is left out of the fingerprint
NOT_FINGERPRINTED = ("companyLogo",)

# Place fields, prefecture first. Municipality names are in the genitive
# and some read like another city: the Athens suburb of Irakleio is given
# as "Dimos Irakleiou", which the city aliases take for Heraklion in Crete.
# The first alias in the text wins, so the prefecture decides.
PLACE_FIELDS = ("state", "city", "municipality", "geoDepartment", "country")
# Most specific first, for role_family when the title gives no signal
DEPARTMENT_FIELDS = ("jobType", "subCategory", "category")


class FeedError(ValueError):
    """A listing page is not the RSS feed, so the site changed something."""


class SkywalkerAdapter(ServesListedPayloads):
    """The detail of a posting is its feed item, since detail pages are off limits."""

    name: ClassVar[str] = "skywalker"
    PARSER_VERSION: ClassVar[str] = "1"

    def list_postings(self, ctx: RunContext) -> Iterator[ListingRef]:
        for category in ctx.config.accounts:
            yield from self._list_category(ctx, category)

    def _list_category(self, ctx: RunContext, category: str) -> Iterator[ListingRef]:
        seen: set[str] = set()
        for page in range(1, ctx.config.max_pages + 1):
            page_url = f"{ctx.config.base_url}?category={category}&page={page}"
            try:
                result = ctx.client.get(page_url)
            except (HttpStatusError, DisallowedByRobots) as err:
                # Skip the rest of this category and keep going with the others
                ctx.log.warning("category_skipped", category=category, url=page_url, error=str(err))
                ctx.listings.record_error(page_url, str(err))
                return

            ctx.listings.save_listing_page(result, account=category)
            new = 0
            for item in feed_items(result.content, page_url):
                url = clean(item.findtext("link"))
                job_id = job_id_from_url(url)
                if url is None or job_id is None:
                    ctx.log.warning("listing_item_skipped", category=category, link=url)
                    continue
                if job_id in seen:
                    continue
                seen.add(job_id)
                new += 1
                self.keep_listed(
                    RawPayload(
                        source=self.name,
                        source_job_id=job_id,
                        url=url,
                        run_date=ctx.run_date,
                        fetched_at=result.fetched_at,
                        content=item_bytes(item),
                        content_type=ITEM_CONTENT_TYPE,
                        account=category,
                    )
                )
                yield ListingRef(
                    source_job_id=job_id,
                    url=url,
                    listing_fingerprint=listing_fingerprint(item),
                    account=category,
                )
            # Past the last page, or the site ignored the page number
            if new == 0:
                return
        ctx.log.warning("max_pages_reached", category=category, max_pages=ctx.config.max_pages)

    def parse(self, payload: RawPayload) -> RawPosting:
        where = payload.object_key or payload.url
        try:
            item = ET.fromstring(payload.content)
        except ET.ParseError as err:
            raise ParseError(f"not XML: {where}") from err
        if item.tag != "item":
            raise ParseError(f"no feed item in {where}")

        return RawPosting(
            source=payload.source,
            source_job_id=payload.source_job_id,
            run_date=payload.run_date,
            scraped_at=payload.fetched_at,
            url=clean(item.findtext("link")) or payload.url,
            title_raw=clean(TITLE_PREFIX.sub("", item.findtext("title") or "")),
            company_raw=clean(item.findtext("company")),
            location_raw=joined(values(item, *PLACE_FIELDS)),
            # on-site, hybrid or remote
            workplace_raw=clean(item.findtext("workType")),
            employment_type_raw=joined(values(item, "employmentType")),
            # The feed has no salary
            salary_raw=None,
            department_raw=joined(values(item, *DEPARTMENT_FIELDS)),
            description_raw=tidy_lines(item.findtext("description") or ""),
            posted_at_raw=clean(item.findtext("fromDate")),
            object_key=payload.object_key,
            parser_version=self.PARSER_VERSION,
        )


def job_id_from_url(url: str | None) -> str | None:
    match = JOB_ID_IN_URL.search(url or "")
    return match.group(1) if match else None


def feed_items(content: bytes, url: str) -> list[ET.Element]:
    try:
        root = ET.fromstring(content)
    except ET.ParseError as err:
        raise FeedError(f"listing page is not XML: {url}") from err
    if root.tag != "rss":
        raise FeedError(f"listing page is not an RSS feed: {url}")
    return root.findall("./channel/item")


def item_bytes(item: ET.Element) -> bytes:
    """One feed item on its own, as UTF-8 XML."""
    # A shallow copy, so dropping the whitespace after the item does not
    # change the page it came from
    item = copy.copy(item)
    item.tail = None
    body: bytes = ET.tostring(item, encoding="utf-8", xml_declaration=True)
    return body


def listing_fingerprint(item: ET.Element) -> str:
    """A hash of the item's fields, in feed order."""
    fields = [
        [child.tag, (child.text or "").strip()]
        for child in item
        if child.tag not in NOT_FINGERPRINTED
    ]
    blob = json.dumps(fields, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def values(item: ET.Element, *tags: str) -> list[str]:
    """The text of every child with one of these tags, in tag order, without repeats.

    The feed repeats a tag for each value, for example one <state> per
    prefecture, and sometimes lists the same category twice.
    """
    found: list[str] = []
    for tag in tags:
        for child in item.findall(tag):
            text = clean(child.text)
            if text and text not in found:
                found.append(text)
    return found


def joined(found: list[str]) -> str | None:
    return ", ".join(found) or None
