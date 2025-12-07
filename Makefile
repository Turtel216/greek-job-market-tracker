# Common tasks for local development and the server.
# Run `make help` to see them.

UV ?= uv
PRE_COMMIT ?= pre-commit
DBT ?= dbt
COMPOSE ?= docker compose

.DEFAULT_GOAL := help
.PHONY: help setup up down lint test dbt-build backup restore

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "} {printf "  %-12s %s\n", $$1, $$2}'

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

backup: ## Dump the warehouse and metabase_app databases
	./scripts/backup.sh

restore: ## Restore a dump into a scratch database
	./scripts/restore.sh
