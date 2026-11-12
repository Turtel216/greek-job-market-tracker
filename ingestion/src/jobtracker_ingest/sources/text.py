"""Small text helpers shared by the source adapters."""

import re
from typing import Any

from bs4 import BeautifulSoup

# Tags that start a new line in the plain text description
BLOCK_TAGS = ("p", "div", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "table")


def clean(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return value.strip() or None


def as_list(value: Any) -> list[Any]:
    """value as a list: a list as it is, None as [], anything else in a list."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def joined(value: Any) -> str | None:
    """One text or a list of texts, cleaned and joined with commas.

    Anything that is not text is left out, so a list of numbers gives None.
    """
    found = [clean(v) for v in as_list(value)]
    return ", ".join(f for f in found if f) or None


def markup_to_text(markup: str) -> str | None:
    """HTML as plain text, one block per line, with whitespace collapsed."""
    soup = BeautifulSoup(markup, "html.parser")
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for tag in soup.find_all(BLOCK_TAGS):
        tag.insert_before("\n")
        tag.insert_after("\n")
    return tidy_lines(soup.get_text())


def tidy_lines(text: str) -> str | None:
    """Plain text with whitespace collapsed inside lines and blank lines dropped."""
    lines = (" ".join(line.split()) for line in text.splitlines())
    return "\n".join(line for line in lines if line) or None


def job_id_from_url(url: str | None, pattern: re.Pattern[str]) -> str | None:
    """The posting id in url, which is the first group of pattern."""
    match = pattern.search(url or "")
    return match.group(1) if match else None
