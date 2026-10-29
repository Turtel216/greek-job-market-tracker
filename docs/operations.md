# Operations

How to run, change and debug the pipeline day to day.
The ingestion CLI has its own notes in
[`ingestion/README.md`](../ingestion/README.md).

## Starting the stack

```sh
cp .env.example .env    # then fill in real values
make setup              # install dev dependencies, dbt and git hooks
make up                 # start Postgres, the object store, Airflow and Metabase
make help               # list every target
```

`make up` also runs three one-off containers: `objectstore-init` creates the
`jobtracker-raw` bucket and its access key, `airflow-init` migrates the
Airflow database, creates the `admin` user (password from
`AIRFLOW_ADMIN_PASSWORD`) and the `http_sources` and `dbt` pools, and
`metabase-init` sets up the dashboards (see [Dashboards](#dashboards)).

The Airflow UI is at <http://localhost:8080> and Metabase at
<http://localhost:3000>. On a server, reach both through an SSH tunnel.

The Postgres init scripts in `infra/postgres/init/` run only on the first
start with an empty volume. After changing them, reset the database with
`docker compose down -v`, or run `make db-tables` to add the raw and ops
tables, such as `ops.dbt_runs`, to an existing one. It also moves an older
`raw.raw_postings` to the natural key that includes `parser_version`.

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

| DAG | Schedule (Europe/Athens) | Does |
| --- | --- | --- |
| `jobtracker_daily` | 06:00 | Extract, load, dbt build, daily summary |
| `jobtracker_watchdog` | 10:00 | Alerts when today's daily run has not succeeded |
| `jobtracker_reparse` | Manual | Loads stored payloads again after a parser fix |

DAGs are paused when first created. Unpause them in the UI once at least
one source is enabled.

### jobtracker_daily

The DAG has one task group per enabled source in `config/sources.yml`,
each running `jobtracker extract` and then `jobtracker load` for that day's
`run_date`. Extract tasks share the `http_sources` pool, so at most 3
sources are fetched at once. A source that blocks us fails without a retry.

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
Airflow logs volume. The dbt steps of every DAG share the `dbt` pool, which
has one slot, so two builds never run at once.

After the dbt steps, `load_dbt_results` runs
`jobtracker load-dbt-results`, which writes every result in those artifacts
to `ops.dbt_runs`: one row per node per dbt invocation, with its status,
failures, message, row count and timing. Like `notify_summary` it is a
teardown task, so it also records the results of a failed build and never
decides whether the run succeeded.

`notify_summary` sends the daily summary on the alert channels: for each
enabled source, the postings that were new, open and closed in today's run
(from `qa.qa_source_status`), then every dbt test that warned or failed and
any dbt step that left no results. It reads the warehouse as the read-only
`bi_reader` role.

### jobtracker_watchdog

Runs at 10:00 and fails, which sends an alert, when today's daily run
failed, is still running or never started. The alert names the last
`run_date` that succeeded.

### Backfilling a parser fix

`dags/jobtracker_reparse.py` loads stored payloads again after a parser fix,
without scraping again, and then runs the same dbt steps as the daily DAG
(without freshness). It only runs when triggered, with three params:
`source` (any source in the registry, enabled or not), `start_date` and
`end_date`. Its dbt artifacts go to `logs/dbt/reparse/<dag run id>/<step>/`,
apart from the daily ones, and its dbt results are loaded into
`ops.dbt_runs` under the Athens date of the reparse.

When a parser breaks, for example after the volume test fails:

1. Fix the parser, add the new page as a fixture, and bump the adapter's
   `PARSER_VERSION`. Without the bump the reparse inserts nothing.
2. Deploy: `make airflow-image && make up`.
3. Trigger `jobtracker_reparse` in the UI with the source and the first and
   last affected `run_date`. Payloads are kept for 30 days, so older days
   cannot be reparsed and keep their old rows.
4. Check the result: `raw.raw_postings` has rows with the new
   `parser_version` next to the old ones, `ops.ingest_runs` shows the new
   version for each day, and the `qa_*` views and marts look right.

The old rows stay in raw, since raw is never updated, and
`stg_raw_postings` reads the newest load of each posting and day.
`snap_postings` records a new version for each posting the fix changed.
A day that fails to load does not stop the others, and the task fails at
the end. Clear the failed tasks to run them again; a second reparse of the
same days inserts nothing new.

This was rehearsed on 2026-10-28 on a local stack. Three days of stored
Greenhouse fixture runs were loaded by a parser that lost the location, so
every job showed city Unknown. A reparse with the fixed parser added nine
rows, and `dim_jobs` showed the right cities again.

### Shared code and alerts

Shared code lives in `dags/common/`:

| Module | Contents |
| --- | --- |
| `defaults.py` | Default task args, the pool names, the registry sources |
| `rundate.py` | `run_date`, the Europe/Athens date of the run |
| `cli.py` | `CliOperator`, which runs a CLI command and keeps its last lines |
| `alerts.py` | Failure and retry alerts |
| `dbt.py` | The commands for the dbt steps |
| `summary.py` | The daily summary message |
| `watchdog.py` | The check behind `jobtracker_watchdog` |

Every task alerts on failure and on retry. Alerts go to the Discord webhook
in `ALERT_DISCORD_WEBHOOK_URL`, and by email to `ALERT_EMAIL` through the
`AIRFLOW_CONN_SMTP_DEFAULT` connection when Discord is not set or does not
answer. A failure alert has the task, `run_date`, a log link and the last
lines of the command's output.

## dbt

The dbt project is in `transform/`. `make setup` (or `make dbt-setup` on
its own) installs dbt with uv into `transform/.venv`, from the same locked
`infra/airflow/requirements-dbt.txt` as the Airflow image, so there is no
need for a system-wide install.

The quickest check is to build everything against a throwaway Postgres
with the init scripts and a made-up sample of postings:

```sh
make test-dbt
```

To build against the running stack instead, run `make dbt-build`.
`transform/profiles.yml` reads the connection from the environment
(`POSTGRES_HOST`, `POSTGRES_PORT`, `WAREHOUSE_DB`, `TRANSFORMER_PASSWORD`,
`DBT_TARGET`, `DBT_THREADS`) and connects as the `transformer` role. dbt
also loads `.env` on its own, without overriding variables that are already
set, so the target only sets `POSTGRES_HOST=localhost`.

### Seeds

Seeds hold the dictionaries and rules that drive matching. They load into
the `staging` schema:

| Seed | Contents |
| --- | --- |
| `skills` | Skills with a stable `skill_id` and a category |
| `skill_aliases` | Words that mark a skill, with a regex for ambiguous ones such as Go, R and C |
| `city_aliases` | Greek and Latin place names mapped to a city and region |
| `company_suffixes` | Legal forms stripped from company names, such as ΑΕ and Ltd |
| `company_aliases` | Other names of a company mapped to one `company_key` |
| `company_placeholders` | Names such as "confidential" that never identify a company, so they are never matched |
| `title_word_map` | Title words dropped or mapped to a synonym before titles are compared for dedup |
| `work_mode_rules`, `seniority_rules`, `role_family_rules` | Keyword rules with a priority; the highest match wins |
| `dedup_labels` | Hand-labeled pairs of postings, used to score deduplication |

Aliases and keywords are written the way `normalize_text` leaves text:
lowercase, no accents, final sigma as σ, single spaces. The tests in
`transform/seeds/_seeds.yml` check this, so a new row in the wrong form
fails `dbt build`. To improve matching, edit the CSV and run
`make test-dbt`.

### Staging

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
More vars drive the later layers: `missed_runs_to_close` (2),
`dedup_window_days` (30) and `ats_sources`, the sources with
`kind: ats_api` in `config/sources.yml`. Keep that list in step with the
registry.

### Snapshot and intermediate

The snapshot `snap_postings` (in the `snapshots` schema) keeps one row per
version of each posting, with a new version whenever its `content_hash`
changes.

The intermediate models are tables in the `intermediate` schema:

| Model | Grain | Contents |
| --- | --- | --- |
| `int_posting_lifecycle` | posting | `first_seen`, `last_seen`, `is_open` and `closed_date`. A posting closes after it is missing from `missed_runs_to_close` successful runs of its source in a row; failed runs in `ops.ingest_runs` do not count |
| `int_posting_skills` | posting and skill | Skills found by matching `skill_aliases` on the normalized title and description, with `matched_in` |
| `int_job_candidate_pairs` | pair of postings | Postings of the same company whose titles have the same words after `title_word_map`, first seen within `dedup_window_days`, and whose city and seniority do not disagree |
| `int_job_matches` | posting | The `job_id` of each posting. Pairs are joined into clusters, and `job_id` is the `posting_key` of the member first seen, so a full refresh gives the same ids |

### Marts

The marts are tables in the `marts` schema, the only one the public
dashboard reads. Their contracts are enforced, so a renamed column or a
changed type fails the build:

| Model | Grain | Contents |
| --- | --- | --- |
| `dim_jobs` | job | Attributes of the primary posting (ATS first, then the one that knows the most, then the earliest), the first stated salary, `first_seen`, `last_seen`, `closed_date`, `is_open`, `days_open` |
| `dim_skills` | skill | `skill_id`, `name`, `category` |
| `bridge_job_skills` | job and skill | Every skill of the job's postings, with `matched_in` |
| `fct_daily_postings` | day, role family, city | New, open and closed jobs on a date spine. Fully remote jobs count under `Remote` |
| `fct_monthly_skill_demand` | month, skill, role family | Jobs with the skill, all open jobs and the share. A job counts in every month it was open |

The `qa_*` views (`qa_unmapped_locations`, `qa_unclassified_titles`,
`qa_salary_parse_failures`, `qa_source_status`) list the values the seeds
do not cover yet and the state of each source. They build in the marts
step.

### Unit tests

Unit tests for the staging rules are in
`transform/models/staging/_unit_tests.yml`, with real Greek and English
examples. The intermediate models, marts and qa views have their own
`_unit_tests.yml` next to them, covering when postings close, the skill
patterns, how postings merge into jobs and the date spines. They run as
part of `dbt build` against the real seed files, so a seed change that
breaks a rule fails the build. dbt needs to create temporary tables for
them; a database created before that grant was added needs it once:

```sh
docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
    -c "grant temporary on database $POSTGRES_DB to transformer"'
```

### Deduplication precision

The target is a precision of at least 95% on 200 hand-labeled pairs.
Two targets help with that:

```sh
make dedup-sample         # score dedup on the made-up sample in tests/dedup
make dedup-label-sample   # write pairs from the running stack to a CSV to label
```

`make dedup-sample` loads `tests/dedup/sample_postings.csv` into a
throwaway Postgres, builds the project and scores it with the
`dedup_precision` analysis against `transform/seeds/dedup_labels.csv`. It
fails under 0.95. The labels in the repo are for the made-up sample; real
labels come from `make dedup-label-sample` once there is enough real data.
Fill in `is_duplicate` in the CSV it writes and copy the labeled rows into
the seed.

## Dashboards

Metabase serves two dashboards, both defined in `metabase/`:

| Dashboard | Collection | Who sees it | Reads |
| --- | --- | --- | --- |
| Greek tech job market | Job market | Anyone with its public link | `marts` only |
| Pipeline health | Pipeline health | Metabase admins | `qa` and `ops` |

The public dashboard answers six questions: skill demand month to month, new jobs per week by role family, work mode by city,
demand by seniority, salary ranges by role and seniority (gross by default,
with the share of jobs that state a salary), and how long jobs stay open.
A role family filter narrows every chart. It shows totals only, never a
title, company or link of a single posting.

The health dashboard shows each source's newest run and how long ago it
loaded, postings parsed per source and day, every load of the last 30
days, dbt test results per day and what did not pass in the newest build,
and the most common values in `qa_unmapped_locations`,
`qa_unclassified_titles` and `qa_salary_parse_failures`. Never share it.

Each file in `metabase/dashboards/` is one dashboard: its collection, its
filters, and every card with its place on the 24 column grid and its chart
settings. Each question's SQL is a file in `metabase/questions/`, and a
filter is a Metabase text tag in an optional clause:

```sql
where is_open [[and role_family = {{role_family}}]]
```

`metabase/provision.py` turns these files into Metabase content through its
API. The `metabase-init` container runs it on `make up`. It creates the
admin from `METABASE_ADMIN_EMAIL` and `METABASE_ADMIN_PASSWORD` on the first
start, connects to the warehouse as `bi_reader` (seeing only `marts`, `qa`
and `ops`), and creates or updates the collections, questions and
dashboards. Questions removed from a file are archived. Only the public
dashboard gets a public link, which the script prints. Changes made to
these dashboards in the Metabase UI are lost on the next run, so edit the
files and run:

```sh
make dashboards
```

Metabase keeps its own data in the `metabase_app` database. Public links
start with `METABASE_SITE_URL`.

## Tests

| Target | Runs | Needs |
| --- | --- | --- |
| `make lint` | Every pre-commit hook: ruff, mypy, sqlfluff, YAML checks, gitleaks | uv, pre-commit |
| `make test` | The ingestion tests, failing when `core/` and `sources/` drop below 85% coverage | uv |
| `make test-db` | The loader's warehouse tests against a throwaway Postgres | Docker |
| `make test-dags` | The DAG tests inside the Airflow image, including a `dbt ls` check that the dbt steps build every node exactly once | Docker, `make airflow-image` |
| `make test-metabase` | The provisioning script and the dashboard files: cards fit the grid, every filter tag has a dashboard filter, public questions read only `marts` | uv |
| `make test-dbt` | `dbt build` with every unit and data test against a throwaway Postgres, then every dashboard question as `bi_reader`, with and without its filters | Docker, `make dbt-setup` |

Before opening a pull request, run:

```sh
make lint test test-db test-dags test-metabase test-dbt
```

## CI

`.github/workflows/ci.yml` runs on every pull request and on pushes to
`main`. Each job calls the same make target as above:

| Job | Runs |
| --- | --- |
| `lint` | `make lint`, then a gitleaks scan of the whole git history |
| `test` | `make test`, with the coverage check, then `make test-metabase` |
| `test-db` | `make test-db`, using the Docker on the runner |
| `dbt` | `make dbt-setup`, then `make test-dbt`, which also runs every dashboard question |

The gitleaks pre-commit hook only looks at staged changes, so CI skips it
and scans every commit instead. When you bump gitleaks in
`.pre-commit-config.yaml`, update the version and checksum in the workflow
too. CI sets `UV_LOCKED=1`, so it fails if `ingestion/uv.lock` is out of
date; run `uv lock` in `ingestion/` and commit the result.

The DAG tests are not in CI yet.
