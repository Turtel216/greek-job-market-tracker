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
