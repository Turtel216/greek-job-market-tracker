# Greek Tech Job Market Tracker

A self-hosted data pipeline that collects Greek tech job postings every day,
cleans and deduplicates them, and turns them into a dashboard of hiring trends:
in-demand skills, hiring roles and cities, remote work share, and how all of
this changes month to month.

The design document in `docs/design.md` is the source of truth for how the
project is built.

## Status

Phase 2, Orchestration. Phase 1 is done: the repo skeleton, tooling,
Postgres, the object store and the first ingestion adapter are in place.
Airflow runs in Compose with the `jobtracker_daily` DAG and its alerts, and
the loader writes each run to `raw` and `ops`; the dbt project is still to
come.

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
make setup              # install dev dependencies and git hooks
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

The image also contains `config/sources.yml`, so rebuild it after changing
the registry or the ingestion package. To change the dbt version, edit
`infra/airflow/requirements-dbt.in` and run `make dbt-lock`.

## DAGs

`dags/jobtracker_daily.py` runs at 06:00 Europe/Athens. It has one task
group per enabled source in `config/sources.yml`, each running
`jobtracker extract` and then `jobtracker load` for that day's `run_date`.
Extract tasks share the `http_sources` pool, so at most 3 sources are
fetched at once. A source that blocks us fails without a retry.

Shared code lives in `dags/common/`:

| Module | Contents |
| --- | --- |
| `defaults.py` | Default task args, the pool name, enabled sources |
| `rundate.py` | `run_date`, the Europe/Athens date of the run |
| `cli.py` | `CliOperator`, which runs a CLI command and keeps its last lines |
| `alerts.py` | Failure and retry alerts |

Every task alerts on failure and on retry. Alerts go to the Discord webhook
in `ALERT_DISCORD_WEBHOOK_URL`, and by email to `ALERT_EMAIL` through the
`AIRFLOW_CONN_SMTP_DEFAULT` connection when Discord is not set or does not
answer. A failure alert has the task, `run_date`, a log link and the last
lines of the command's output.

DAGs are paused when first created. Unpause `jobtracker_daily` in the UI
once at least one source is enabled.

The DAG tests in `tests/dags/` need Airflow, so they run inside the Airflow
image:

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
make lint test test-db test-dags dbt-build
```
