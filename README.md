# Greek Tech Job Market Tracker

A self-hosted data pipeline that collects Greek tech job postings every day,
cleans and deduplicates them, and turns them into a dashboard of hiring trends:
in-demand skills, hiring roles and cities, remote work share, and how all of
this changes month to month.

The design document in `docs/design.md` is the source of truth for how the
project is built.

## Status

Phase 3, Modeling. Phases 1 and 2 are done: the repo skeleton, tooling,
Postgres, the object store and the ingestion adapters are in place.
Airflow runs in Compose with the `jobtracker_daily` DAG and its alerts, and
the loader writes each run to `raw` and `ops`. CI runs lint and the Python
tests on every pull request. The dbt project builds every layer: seeds,
staging, the posting snapshot, lifecycle and skill extraction, and the marts
with enforced contracts. Deduplication comes in Phase 4; until then every
posting is a job of its own.

## Repository layout

| Folder | Contents |
| --- | --- |
| `config/` | Source registry (`sources.yml`) |
| `ingestion/` | Python package `jobtracker_ingest` |
| `dags/` | Airflow DAGs |
| `transform/` | dbt project |
| `infra/` | Dockerfiles and service config |
| `metabase/` | Exported dashboard definitions |
| `scripts/` | Backup and restore scripts |

## Requirements

- [uv](https://docs.astral.sh/uv/) (installs Python 3.12 for you)
- [pre-commit](https://pre-commit.com/)
- Docker with the Compose plugin
- GNU Make

## Getting started

```sh
cp .env.example .env    # then fill in real values
make setup              # install dev dependencies, dbt and git hooks
make up                 # start Postgres, the object store and Airflow
make help               # list every target
```

`make up` also runs two one-off containers: `objectstore-init` creates the
`jobtracker-raw` bucket and its access key, and `airflow-init` migrates the
Airflow database, creates the `admin` user (password from
`AIRFLOW_ADMIN_PASSWORD`) and the `http_sources` pool. The Airflow UI is at
<http://localhost:8080>; on the server, reach it through an SSH tunnel. The Postgres init scripts in
`infra/postgres/init/` run only on the first start with an empty volume, so
after changing them reset the database with `docker compose down -v`, or
run `make db-tables` to add the raw and ops tables to an existing one.

## Airflow image

`infra/airflow/Dockerfile` builds the image that runs every pipeline task.
It adds two things to the official Airflow image, each in its own virtualenv
so their pinned dependencies never touch Airflow's:

- the ingestion package, installed from `ingestion/uv.lock`, as `jobtracker`
- dbt Core with dbt-postgres, installed from
  `infra/airflow/requirements-dbt.txt`, as `dbt`

```sh
make airflow-image
docker run --rm --entrypoint bash jobtracker-airflow:dev -c "jobtracker --help && dbt --version"
```

The image also contains `config/sources.yml` and the dbt project from
`transform/`, so rebuild it after changing the registry, the ingestion
package or any dbt model, seed or macro. `DBT_PROJECT_DIR` and
`DBT_PROFILES_DIR` point dbt at the project. To change the dbt version,
edit `infra/airflow/requirements-dbt.in` and run `make dbt-lock`.

## DAGs

`dags/jobtracker_daily.py` runs at 06:00 Europe/Athens. It has one task
group per enabled source in `config/sources.yml`, each running
`jobtracker extract` and then `jobtracker load` for that day's `run_date`.
Extract tasks share the `http_sources` pool, so at most 3 sources are
fetched at once. A source that blocks us fails without a retry.

Once every load is done, the dbt steps run one after the other:

| Task | Command | Retries |
| --- | --- | --- |
| `dbt_freshness` | `dbt source freshness` | 0 |
| `dbt_staging` | `dbt build --selector staging` (seeds and staging) | 0 |
| `dbt_snapshot` | `dbt build --selector snapshots` | 1 |
| `dbt_intermediate` | `dbt build --selector intermediate` | 0 |
| `dbt_marts` | `dbt build --selector marts` | 0 |

The selectors are in `transform/selectors.yml`. Each step runs its layer's
tests, so a failed test stops the later steps and the marts keep the last
good data. `dbt_freshness` waits for every load and runs when at least one
of them succeeded, so one broken source never blocks the others; when none
did, no dbt step runs. dbt unit tests run in CI, not in the DAG. Each step
keeps its artifacts and `dbt.log` in `logs/dbt/<run_date>/<step>/` on the
Airflow logs volume.

Shared code lives in `dags/common/`:

| Module | Contents |
| --- | --- |
| `defaults.py` | Default task args, the pool name, enabled sources |
| `rundate.py` | `run_date`, the Europe/Athens date of the run |
| `cli.py` | `CliOperator`, which runs a CLI command and keeps its last lines |
| `alerts.py` | Failure and retry alerts |
| `dbt.py` | The commands for the dbt steps |

Every task alerts on failure and on retry. Alerts go to the Discord webhook
in `ALERT_DISCORD_WEBHOOK_URL`, and by email to `ALERT_EMAIL` through the
`AIRFLOW_CONN_SMTP_DEFAULT` connection when Discord is not set or does not
answer. A failure alert has the task, `run_date`, a log link and the last
lines of the command's output.

DAGs are paused when first created. Unpause `jobtracker_daily` in the UI
once at least one source is enabled.

The DAG tests in `tests/dags/` need Airflow, so they run inside the Airflow
image. They also check with `dbt ls` that the dbt steps build every model,
seed, snapshot and test exactly once:

```sh
make airflow-image   # after changing the image
make test-dags
```

The loader's warehouse tests need a real Postgres. This starts a throwaway
one with the init scripts, runs them and removes it:

```sh
make test-db
```

Before opening a pull request, run:

```sh
make lint test test-db test-dags test-dbt
```

`make test` also measures coverage and fails when `core/` and `sources/`
together drop below 85%.

## dbt

The dbt project is in `transform/`. `make setup` (or `make dbt-setup` on
its own) installs dbt with uv into `transform/.venv`, from the same locked
`infra/airflow/requirements-dbt.txt` as the Airflow image, so there is no
need for a system-wide install.

The quickest check is to build everything against a throwaway Postgres
with the init scripts, like `make test-db` does:

```sh
make test-dbt
```

To build against the running stack instead, run `make dbt-build`.
`transform/profiles.yml` reads the connection from the environment
(`POSTGRES_HOST`, `POSTGRES_PORT`, `WAREHOUSE_DB`, `TRANSFORMER_PASSWORD`,
`DBT_TARGET`, `DBT_THREADS`) and connects as the `transformer` role. dbt
also loads `.env` on its own, without overriding variables that are already
set, so the target only sets `POSTGRES_HOST=localhost`.

Seeds hold the dictionaries and rules that drive matching. They load into
the `staging` schema:

| Seed | Contents |
| --- | --- |
| `skills` | Skills with a stable `skill_id` and a category |
| `skill_aliases` | Words that mark a skill, with a regex for ambiguous ones such as Go, R and C |
| `city_aliases` | Greek and Latin place names mapped to a city and region |
| `company_suffixes` | Legal forms stripped from company names, such as ΑΕ and Ltd |
| `company_aliases` | Other names of a company mapped to one `company_key` |
| `work_mode_rules`, `seniority_rules`, `role_family_rules` | Keyword rules with a priority; the highest match wins |

Aliases and keywords are written the way `normalize_text` leaves text:
lowercase, no accents, final sigma as σ, single spaces. The tests in
`transform/seeds/_seeds.yml` check this, so a new row in the wrong form
fails `dbt build`. To improve matching, edit the CSV and run
`make test-dbt`.

The staging models read `raw.raw_postings`:

| Model | Grain | Contents |
| --- | --- | --- |
| `stg_raw_postings` (view) | posting and run_date | `posting_key` and tidy display text, nothing heavier |
| `stg_postings` (table) | posting | The newest row of each posting with normalized text, `company_key`, city, work mode, seniority, role family and monthly salary |

The logic lives in macros in `transform/macros/`:

| Macro | Does |
| --- | --- |
| `normalize_text` | Lowercase, strip accents, final sigma to σ, punctuation other than `+ # .` to spaces |
| `match_rules` | Picks the value of the best rule in a keyword rule seed |
| `parse_salary` | Turns salary text into a monthly range with currency, basis and period; reads gross or net from a salary sentence in the description when the salary text says neither |

The salary settings are dbt vars in `transform/dbt_project.yml`:
`salaries_per_year` (14), the annual threshold and the monthly bounds.
Two more vars drive the later layers: `missed_runs_to_close` (2) and
`ats_sources`, the sources with `kind: ats_api` in `config/sources.yml`.
Keep that list in step with the registry.

The snapshot `snap_postings` (in the `snapshots` schema) keeps one row per
version of each posting, with a new version whenever its `content_hash`
changes.

The intermediate models are tables in the `intermediate` schema:

| Model | Grain | Contents |
| --- | --- | --- |
| `int_posting_lifecycle` | posting | `first_seen`, `last_seen`, `is_open` and `closed_date`. A posting closes after it is missing from `missed_runs_to_close` successful runs of its source in a row; failed runs in `ops.ingest_runs` do not count |
| `int_posting_skills` | posting and skill | Skills found by matching `skill_aliases` on the normalized title and description, with `matched_in` |
| `int_job_matches` | posting | The `job_id` of each posting. For now `job_id` is the `posting_key`; deduplication replaces this model in Phase 4 |

The marts are tables in the `marts` schema, the only one Metabase reads.
Their contracts are enforced, so a renamed column or a changed type fails
the build:

| Model | Grain | Contents |
| --- | --- | --- |
| `dim_jobs` | job | Attributes of the primary posting (ATS first, then the one that knows the most, then the earliest), the first stated salary, `first_seen`, `last_seen`, `closed_date`, `is_open`, `days_open` |
| `dim_skills` | skill | `skill_id`, `name`, `category` |
| `bridge_job_skills` | job and skill | Every skill of the job's postings, with `matched_in` |
| `fct_daily_postings` | day, role family, city | New, open and closed jobs on a date spine. Fully remote jobs count under `Remote` |
| `fct_monthly_skill_demand` | month, skill, role family | Jobs with the skill, all open jobs and the share. A job counts in every month it was open |

Unit tests for these rules are in `transform/models/staging/_unit_tests.yml`,
with real Greek and English examples. The intermediate models and marts
have their own `_unit_tests.yml` next to them, covering when postings
close, the skill patterns, how postings merge into jobs and the date
spines. They run as part of `dbt build`
against the real seed files, so a seed change that breaks a rule fails the
build. dbt needs to create temporary tables for them; a database created
before that grant was added needs it once:

```sh
docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
    -c "grant temporary on database $POSTGRES_DB to transformer"'
```

## CI

`.github/workflows/ci.yml` runs on every pull request and on pushes to
`main`. Each job calls the same make target as above:

| Job | Runs |
| --- | --- |
| `lint` | `make lint`, then a gitleaks scan of the whole git history |
| `test` | `make test`, with the coverage check |
| `test-db` | `make test-db`, using the Docker on the runner |
| `dbt` | `make dbt-setup`, then `make test-dbt` |

The gitleaks pre-commit hook only looks at staged changes, so CI skips it
and scans every commit instead. When you bump gitleaks in
`.pre-commit-config.yaml`, update the version and checksum in the workflow
too. CI sets `UV_LOCKED=1`, so it fails if `ingestion/uv.lock` is out of
date; run `uv lock` in `ingestion/` and commit the result.

The DAG tests are not in CI yet.
