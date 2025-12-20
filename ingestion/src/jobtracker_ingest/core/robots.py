"""robots.txt rules for one host.

Fetching is left to the HTTP client so the robots.txt request goes through
the same rate limit as everything else. This module only turns the
response into rules, following RFC 9309:

- 2xx: parse the file.
- 4xx: no usable rules, everything is allowed.
- 5xx or no response: assume everything is disallowed.

401 and 403 are treated as a block by the client before we get here.
"""

from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

# Product token we match against User-agent lines in robots.txt
ROBOTS_AGENT = "GreekTechJobsTracker"


def robots_url(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}/robots.txt"


class RobotsRules:
    def __init__(self, parser: RobotFileParser, reason: str) -> None:
        self._parser = parser
        self.reason = reason

    @classmethod
    def from_response(cls, status: int | None, body: str = "") -> "RobotsRules":
        parser = RobotFileParser()
        if status is None or status >= 500 or status in (401, 403):
            parser.parse(["User-agent: *", "Disallow: /"])
            return cls(parser, f"robots.txt unavailable ({status}), disallow all")
        if 400 <= status < 500:
            parser.parse([])
            return cls(parser, f"no robots.txt ({status}), allow all")
        parser.parse(body.splitlines())
        return cls(parser, "parsed")

    def can_fetch(self, url: str) -> bool:
        return self._parser.can_fetch(ROBOTS_AGENT, url)

    @property
    def crawl_delay(self) -> float | None:
        delay = self._parser.crawl_delay(ROBOTS_AGENT)
        return float(delay) if delay is not None else None
