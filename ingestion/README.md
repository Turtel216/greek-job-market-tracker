# jobtracker-ingest

Fetches job postings from the sources in `config/sources.yml`, stores raw
payloads in the object store and loads parsed rows into `raw.raw_postings`.

## HTTP client

Every request to a source goes through `core.http.PoliteClient`, which
enforces the politeness rules in the design doc: an identifying User-Agent,
a per host delay with jitter, timeouts, retries with backoff, robots.txt and
the stop conditions for blocks. Adapters never call `requests` directly.

```python
from jobtracker_ingest.core.http import PoliteClient, user_agent_from_env

client = PoliteClient(user_agent_from_env(), min_delay_seconds=3)
result = client.get("https://boards-api.greenhouse.io/v1/boards/example/jobs")
```

Create one client per source run. After a block it refuses every request,
which stops the source for the day. `client.stats.as_dict()` has the counts
for the run manifest.

## Extract

`jobtracker extract` fetches one source for one run date and stores every
payload in the object store:

```sh
jobtracker extract --source greenhouse --run-date 2026-10-25
```

It reads the source from `config/sources.yml` (or the file in
`JOBTRACKER_SOURCES`) and refuses a source that is not enabled. Payloads go
under `raw/<source>/<run_date>/<run_id>/`, and `_manifest.json` is written
last with the counts, HTTP stats, every posting seen and the key of each
stored detail. A run that fails, for example on a block or zero listings,
leaves no manifest, so the loader ignores it.

## Adding a source

Write an adapter in `sources/` that follows `sources/base.py`, register it
in `sources/__init__.py`, and add its fixtures under
`tests/fixtures/<source>/` with the expected parse output in `expected/`.
`parse` must be pure, so it can run again on stored payloads.

The first adapter is `sources/greenhouse.py`, for the public Greenhouse Job
Board API. Its fixtures are shaped like real API responses but use a made
up company; add trimmed real responses during onboarding.
