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

The standard library XML parser never loads external entities, and the
expat it ships with limits entity expansion, so it is safe on feed data.
"""

import re
import xml.etree.ElementTree as ET
from typing import ClassVar

from jobtracker_ingest.core.models import RawPayload, RawPosting
from jobtracker_ingest.sources.text import clean, tidy_lines

# Every feed title starts with "Available job position: " in Greek
TITLE_PREFIX = re.compile(r"^\s*Διαθέσιμη θέση εργασίας\s*:\s*")

# Place fields, prefecture first. Municipality names are in the genitive
# and some read like another city: the Athens suburb of Irakleio is given
# as "Dimos Irakleiou", which the city aliases take for Heraklion in Crete.
# The first alias in the text wins, so the prefecture decides.
PLACE_FIELDS = ("state", "city", "municipality", "geoDepartment", "country")
# Most specific first, for role_family when the title gives no signal
DEPARTMENT_FIELDS = ("jobType", "subCategory", "category")


class ParseError(ValueError):
    """The payload is not a skywalker feed item."""


class SkywalkerAdapter:
    name: ClassVar[str] = "skywalker"
    PARSER_VERSION: ClassVar[str] = "1"

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
