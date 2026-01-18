# Common tasks for local development and the server.
# Run `make help` to see them.

UV ?= uv
PRE_COMMIT ?= pre-commit
DBT ?= dbt
COMPOSE ?= docker compose
AIRFLOW_IMAGE ?= jobtracker-airflow:dev

.DEFAULT_GOAL := help
.PHONY: help setup up down lint test dbt-build airflow-image dbt-lock backup restore

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
