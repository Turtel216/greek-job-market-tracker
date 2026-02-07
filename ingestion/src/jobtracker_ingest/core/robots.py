"""robots.txt rules for one host.

Fetching is left to the HTTP client so the robots.txt request goes through
the same rate limit as everything else. This module only turns the
response into rules, following RFC 9309:

- 2xx: parse the file.
- 4xx: no usable rules, everything is allowed.
- 5xx or no response: assume everything is disallowed.

401 and 403 are treated as a block by the client before we get here.

The standard library's RobotFileParser is not used because it applies the
first rule that matches, so a file that starts with "Allow: /" would allow
every path. RFC 9309 says the longest matching rule wins, with * and $ as
wildcards, which is what sites write their files for.
"""

import re
from dataclasses import dataclass, field
from functools import lru_cache
from urllib.parse import urlsplit

# Product token we match against User-agent lines in robots.txt
ROBOTS_AGENT = "GreekTechJobsTracker"


def robots_url(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}/robots.txt"


@dataclass(frozen=True)
class Rule:
    allow: bool
    pattern: str

    def matches(self, path: str) -> bool:
        return pattern_regex(self.pattern).match(path) is not None


@dataclass
class Group:
    rules: list[Rule] = field(default_factory=list)
    crawl_delay: float | None = None


@lru_cache(maxsize=1024)
def pattern_regex(pattern: str) -> re.Pattern[str]:
    """* matches any characters and a trailing $ anchors the end."""
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    regex = ".*".join(re.escape(part) for part in body.split("*"))
    return re.compile(regex + ("$" if anchored else ""))


def parse_groups(body: str) -> dict[str, Group]:
    """Groups by lowercased user agent. Repeated groups for one agent are merged."""
    groups: dict[str, Group] = {}
    agents: list[str] = []
    # A User-agent line after a rule starts a new group
    in_rules = False
    for raw in body.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, value = (part.strip() for part in line.split(":", 1))
        key = key.lower()

        if key == "user-agent":
            if in_rules:
                agents, in_rules = [], False
            # "Name/1.0" names the same product as "Name"
            agent = value.split("/", 1)[0].strip().lower()
            agents.append(agent)
            groups.setdefault(agent, Group())
            continue

        if key in ("allow", "disallow", "crawl-delay"):
            in_rules = True
        for agent in agents:
            group = groups[agent]
            if key in ("allow", "disallow") and value:
                group.rules.append(Rule(allow=key == "allow", pattern=value))
            elif key == "crawl-delay" and group.crawl_delay is None:
                group.crawl_delay = parse_delay(value)
    return groups


def parse_delay(value: str) -> float | None:
    try:
        delay = float(value)
    except ValueError:
        return None
    return delay if delay >= 0 else None


def path_of(url: str) -> str:
    parts = urlsplit(url)
    path = parts.path or "/"
    return f"{path}?{parts.query}" if parts.query else path


class RobotsRules:
    def __init__(self, group: Group, reason: str) -> None:
        self._group = group
        self.reason = reason

    @classmethod
    def from_response(cls, status: int | None, body: str = "") -> "RobotsRules":
        if status is None or status >= 500 or status in (401, 403):
            group = Group(rules=[Rule(allow=False, pattern="/")])
            return cls(group, f"robots.txt unavailable ({status}), disallow all")
        if 400 <= status < 500:
            return cls(Group(), f"no robots.txt ({status}), allow all")

        groups = parse_groups(body)
        # Our own group wins; the * group only applies when there is none
        group = groups.get(ROBOTS_AGENT.lower()) or groups.get("*") or Group()
        return cls(group, "parsed")

    def can_fetch(self, url: str) -> bool:
        path = path_of(url)
        if path == "/robots.txt":
            return True
        matched = [rule for rule in self._group.rules if rule.matches(path)]
        if not matched:
            return True
        # The longest pattern wins, and on a tie allow wins
        best = max(matched, key=lambda rule: (len(rule.pattern), rule.allow))
        return best.allow

    @property
    def crawl_delay(self) -> float | None:
        return self._group.crawl_delay
