#!/usr/bin/env bash
# Runs `dbt build` against a throwaway database: seeds, models and every
# test. Extra arguments go to dbt build. Set DBT to use another dbt binary.
set -euo pipefail

cd "$(dirname "$0")/.."
DBT=${DBT:-$PWD/transform/.venv/bin/dbt}
source scripts/lib/throwaway-postgres.sh

export POSTGRES_HOST=127.0.0.1
export POSTGRES_PORT="$TEST_DB_PORT"
export WAREHOUSE_DB=warehouse
export TRANSFORMER_PASSWORD="$TEST_DB_PASSWORD"

cd transform
"$DBT" build "$@"
