# Greek Tech Job Market Tracker

A self-hosted data pipeline that collects Greek tech job postings every day,
cleans and deduplicates them, and turns them into a dashboard of hiring trends:
in-demand skills, hiring roles and cities, remote work share, and how all of
this changes month to month.

The design document in `docs/design.md` is the source of truth for how the
project is built.

## Status

Phase 1, Foundations. The repo skeleton and tooling are in place; the stack,
ingestion and dbt project are still to come.

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
make help               # list every target
```

Before opening a pull request, run:

```sh
make lint test dbt-build
```
