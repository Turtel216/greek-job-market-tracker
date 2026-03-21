#!/usr/bin/env bash
# Runs the loader's Postgres tests against a throwaway database. Extra
# arguments go to pytest.
set -euo pipefail

cd "$(dirname "$0")/.."
source scripts/lib/throwaway-postgres.sh

export JOBTRACKER_TEST_ADMIN_DSN="postgresql://postgres:$TEST_DB_PASSWORD@127.0.0.1:$TEST_DB_PORT/warehouse"
export JOBTRACKER_TEST_LOADER_DSN="postgresql://loader:$TEST_DB_PASSWORD@127.0.0.1:$TEST_DB_PORT/warehouse"

cd ingestion
uv run pytest -m postgres "$@"
