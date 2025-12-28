"""Read config/sources.yml, the only list of sources.

A source must not be enabled until its terms and robots.txt have been
reviewed, so loading fails if an enabled entry is missing either date.
"""

import os
from datetime import date
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

# config/sources.yml at the repo root. Set JOBTRACKER_SOURCES when the
# package is installed somewhere else, for example in the Airflow image.
REPO_REGISTRY = Path(__file__).resolve().parents[4] / "config" / "sources.yml"


class SourceConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    kind: Literal["ats_api", "job_board_html"]
    enabled: bool = False
    base_url: str
    accounts: list[str] = Field(default_factory=list)
    min_delay_seconds: float = Field(default=3, ge=3)
    max_pages: int = Field(default=50, ge=1)
    render_js: bool = False
    terms_reviewed_on: date | None = None
    robots_reviewed_on: date | None = None
    contact: str | None = None

    @model_validator(mode="after")
    def reviewed_before_enabled(self) -> Self:
        if self.enabled and not (self.terms_reviewed_on and self.robots_reviewed_on):
            raise ValueError(
                f"source {self.name} is enabled but terms_reviewed_on or "
                "robots_reviewed_on is missing"
            )
        return self


class Registry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    sources: list[SourceConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_names(self) -> Self:
        names = [s.name for s in self.sources]
        duplicates = sorted({n for n in names if names.count(n) > 1})
        if duplicates:
            raise ValueError(f"duplicate source names: {', '.join(duplicates)}")
        return self

    def get(self, name: str) -> SourceConfig:
        for source in self.sources:
            if source.name == name:
                return source
        raise KeyError(f"no source named {name} in the registry")

    @property
    def enabled(self) -> list[SourceConfig]:
        return [s for s in self.sources if s.enabled]


def load_registry(path: Path | None = None) -> Registry:
    path = path or Path(os.environ.get("JOBTRACKER_SOURCES", REPO_REGISTRY))
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return Registry.model_validate(data)
