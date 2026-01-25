# Common tasks for local development and the server.
# Run `make help` to see them.

UV ?= uv
PRE_COMMIT ?= pre-commit
DBT ?= dbt
COMPOSE ?= docker compose
AIRFLOW_IMAGE ?= jobtracker-airflow:dev

.DEFAULT_GOAL := help
.PHONY: help setup up down lint test test-dags dbt-build airflow-image dbt-lock backup restore

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "} {printf "  %-14s %s\n", $$1, $$2}'

setup: ## Install dev tools and git hooks
	cd ingestion && $(UV) sync
	$(PRE_COMMIT) install

up: ## Start the whole stack
	$(COMPOSE) up -d

down: ## Stop the whole stack
	$(COMPOSE) down

lint: ## Run every pre-commit check on all files
	$(PRE_COMMIT) run --all-files

test: ## Run the Python tests
	cd ingestion && $(UV) run pytest

# The DAG tests need Airflow, so they run in the Airflow image with the repo
# mounted. pytest goes into a throwaway folder, not the image.
test-dags: ## Run the DAG tests inside the Airflow image
	docker run --rm -v "$(CURDIR)":/opt/jobtracker/repo:ro -w /opt/jobtracker/repo \
		--entrypoint bash $(AIRFLOW_IMAGE) -c \
		"uv pip install --quiet --target /tmp/pytest pytest==9.1.1 \
		&& PYTHONPATH=/tmp/pytest python -m pytest -p no:cacheprovider tests/dags"

dbt-build: ## Build and test the dbt project
	@if [ -f transform/dbt_project.yml ]; then \
		cd transform && $(DBT) build; \
	else \
		echo "No dbt project yet, skipping"; \
	fi

airflow-image: ## Build the Airflow image with the ingestion CLI and dbt
	docker build -f infra/airflow/Dockerfile -t $(AIRFLOW_IMAGE) .

dbt-lock: ## Regenerate the locked dbt requirements for the Airflow image
	$(UV) pip compile infra/airflow/requirements-dbt.in --python-version 3.12 \
		--universal --no-header --quiet -o infra/airflow/requirements-dbt.txt

backup: ## Dump the warehouse and metabase_app databases
	./scripts/backup.sh

restore: ## Restore a dump into a scratch database
	./scripts/restore.sh
