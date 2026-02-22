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

Before the first request it reads `ops.posting_fingerprints` and fetches a
posting's detail only when the posting is new, its listing fingerprint
changed, or the stored detail is more than 14 days old. Every other posting
is still listed in the manifest, without a detail, and the loader reuses
its last stored detail. So extract needs the warehouse too, and fails
without sending a request when the warehouse is down.

The command exits with 1 on a failure worth retrying and with 3 when the
source blocked us (401, 403, a CAPTCHA or a login wall). Airflow does not
retry a block, because the source is stopped for the day.

## Load

`jobtracker load` parses the newest complete run of one source for one run
date and writes it to the warehouse as the `loader` role:

```sh
jobtracker load --source greenhouse --run-date 2026-10-25
```

Each payload is parsed by the source adapter and scrubbed by `privacy.py`,
which replaces emails, phone numbers and contact names with `[email]`,
`[phone]` and `[contact]`. Then one transaction writes:

- `raw.raw_postings`: one row per posting for the day. Rows that are
  already there are left alone, since raw is never updated.
- `ops.posting_fingerprints`: the listing fingerprint and the stored
  detail of each posting, for the next extract.
- `ops.ingest_runs`: one row per extract run with the counts, timings and
  HTTP stats. Loading the same run again replaces its row.

A posting that fails to parse, or whose stored detail is gone, is logged
and skipped. The load fails, and records a `failed` run, only when nothing
could be parsed from a non-empty listing. It exits with 1 on any failure,
including a missing run or an unreachable database.

It connects with `POSTGRES_HOST`, `POSTGRES_PORT`, `WAREHOUSE_DB` and
`LOADER_PASSWORD` (and `LOADER_USER`, which defaults to `loader`). The
tables are in `infra/postgres/init/03-raw-and-ops-tables.sql`; on a
database that already exists, create them with `make db-tables`.

The warehouse tests in `tests/loader/test_postgres.py` need a real
Postgres and are skipped by `make test`. `make test-db` starts a throwaway
one with the init scripts and runs them.

## Adding a source

Write an adapter in `sources/` that follows `sources/base.py`, register it
in `sources/__init__.py`, and add its fixtures under
`tests/fixtures/<source>/` with the expected parse output in `expected/`.
`parse` must be pure, so it can run again on stored payloads.

The first adapter is `sources/greenhouse.py`, for the public Greenhouse Job
Board API. Its fixtures are shaped like real API responses but use a made
up company; add trimmed real responses during onboarding.

`sources/kariera.py` reads the kariera.gr job board, one registry account
per listing category such as `it-jobs`. It takes almost everything from the
schema.org JSON-LD on each page and only uses the Next.js flight data for
the remote work field, which JSON-LD has no value for. It never calls the
site's JSON endpoints, which robots.txt disallows. Its fixtures copy the
structure of real pages with made up companies and texts, because the
site's terms do not allow passing its content on. The source is disabled
until its terms review is signed off; see the notes in
`config/sources.yml`.
