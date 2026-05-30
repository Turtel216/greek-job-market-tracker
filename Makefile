# Common tasks for local development and the server.
# Run `make help` to see them.

UV ?= uv
PRE_COMMIT ?= pre-commit
# dbt lives in its own venv, installed by `make dbt-setup` from the same
# locked requirements as the Airflow image
DBT_VENV ?= transform/.venv
DBT ?= $(CURDIR)/$(DBT_VENV)/bin/dbt
COMPOSE ?= docker compose
AIRFLOW_IMAGE ?= jobtracker-airflow:dev

.DEFAULT_GOAL := help
.PHONY: help setup dbt-setup up down db-tables lint test test-db test-dags dbt-build test-dbt dedup-label-sample airflow-image dbt-lock backup restore

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "} {printf "  %-14s %s\n", $$1, $$2}'

setup: dbt-setup ## Install dev tools, dbt and git hooks
	cd ingestion && $(UV) sync
	$(PRE_COMMIT) install

dbt-setup: ## Install dbt into transform/.venv
	$(UV) venv --python 3.12 --allow-existing $(DBT_VENV)
	$(UV) pip install --python $(DBT_VENV)/bin/python --no-deps \
		-r infra/airflow/requirements-dbt.txt

up: ## Start the whole stack
	$(COMPOSE) up -d

down: ## Stop the whole stack
	$(COMPOSE) down

# The init scripts only run on an empty volume. This applies the raw and
# ops tables to a database that already exists; it is safe to run again.
db-tables: ## Create the raw and ops tables on a running stack
	$(COMPOSE) exec -T postgres sh -c 'psql -v ON_ERROR_STOP=1 -U "$$POSTGRES_USER" \
		-d "$$POSTGRES_DB" -f /docker-entrypoint-initdb.d/03-raw-and-ops-tables.sql'

lint: ## Run every pre-commit check on all files
	$(PRE_COMMIT) run --all-files --show-diff-on-failure

# The design asks for at least 85% coverage on core/ and sources/, so the
# second step fails the target when those two folders fall below it.
test: ## Run the Python tests with coverage
	cd ingestion && $(UV) run pytest --cov
	@echo "Coverage of core/ and sources/ (at least 85%):"
	@cd ingestion && $(UV) run coverage report --format=total --fail-under=85 \
		--include='src/jobtracker_ingest/core/*,src/jobtracker_ingest/sources/*'

test-db: ## Run the loader tests against a throwaway Postgres
	./scripts/test-db.sh

# The DAG tests need Airflow, so they run in the Airflow image with the repo
# mounted. pytest goes into a throwaway folder, not the image.
test-dags: ## Run the DAG tests inside the Airflow image
	docker run --rm -v "$(CURDIR)":/opt/jobtracker/repo:ro -w /opt/jobtracker/repo \
		--entrypoint bash $(AIRFLOW_IMAGE) -c \
		"uv pip install --quiet --target /tmp/pytest pytest==9.1.1 \
		&& PYTHONPATH=/tmp/pytest python -m pytest -p no:cacheprovider tests/dags"

# dbt reads the rest of the connection from .env by itself. The host there
# is the one Compose services use, so point it at the published port.
dbt-build: ## Build and test the dbt project on a running stack
	cd transform && POSTGRES_HOST=localhost $(DBT) build

test-dbt: ## Build and test the dbt project against a throwaway Postgres
	DBT="$(DBT)" ./scripts/test-dbt.sh

dedup-label-sample: ## Write pairs of postings to label for dedup on a running stack
	POSTGRES_HOST=localhost DBT="$(DBT)" ./scripts/dedup-label-sample.sh

airflow-image: ## Build the Airflow image with the ingestion CLI and dbt
	docker build -f infra/airflow/Dockerfile -t $(AIRFLOW_IMAGE) .

dbt-lock: ## Regenerate the locked dbt requirements for the Airflow image
	$(UV) pip compile infra/airflow/requirements-dbt.in --python-version 3.12 \
		--universal --no-header --quiet -o infra/airflow/requirements-dbt.txt

backup: ## Dump the warehouse and metabase_app databases
	./scripts/backup.sh

restore: ## Restore a dump into a scratch database
	./scripts/restore.sh
