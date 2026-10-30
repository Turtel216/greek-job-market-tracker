"""Source adapters, one module per source. Register new ones in ADAPTERS."""

from jobtracker_ingest.sources.base import SourceAdapter
from jobtracker_ingest.sources.greenhouse import GreenhouseAdapter
from jobtracker_ingest.sources.kariera import KarieraAdapter
from jobtracker_ingest.sources.skywalker import SkywalkerAdapter

ADAPTERS: dict[str, type[SourceAdapter]] = {
    GreenhouseAdapter.name: GreenhouseAdapter,
    KarieraAdapter.name: KarieraAdapter,
    SkywalkerAdapter.name: SkywalkerAdapter,
}


def get_adapter(name: str) -> SourceAdapter:
    try:
        return ADAPTERS[name]()
    except KeyError:
        raise KeyError(f"no adapter for source {name}") from None
