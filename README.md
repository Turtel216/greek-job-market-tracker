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
The Airflow image is built; the DAGs, the loader and the dbt project are
still to come.

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
make up                 # start Postgres and the object store
make help               # list every target
```

`make up` also runs a one-off `objectstore-init` container that creates the
`jobtracker-raw` bucket and its access key. The Postgres init scripts in
`infra/postgres/init/` run only on the first start with an empty volume, so
after changing them reset the database with `docker compose down -v`.

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

Before opening a pull request, run:

```sh
make lint test dbt-build
```
