"""Small text helpers shared by the source adapters."""

from typing import Any

from bs4 import BeautifulSoup

# Tags that start a new line in the plain text description
BLOCK_TAGS = ("p", "div", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "table")


def clean(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return value.strip() or None


def markup_to_text(markup: str) -> str | None:
    """HTML as plain text, one block per line, with whitespace collapsed."""
    soup = BeautifulSoup(markup, "html.parser")
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for tag in soup.find_all(BLOCK_TAGS):
        tag.insert_before("\n")
        tag.insert_after("\n")
    lines = (" ".join(line.split()) for line in soup.get_text().splitlines())
    return "\n".join(line for line in lines if line) or None
