#!/usr/bin/env bash
# Runs `dbt build` against a throwaway database: seeds, models and every
# test. The made-up postings in tests/dedup are loaded into raw first, so
# the data tests run on real rows and not on empty tables. Extra arguments
# go to dbt build. Set DBT to use another dbt binary.
set -euo pipefail

cd "$(dirname "$0")/.."
DBT=${DBT:-$PWD/transform/.venv/bin/dbt}
source scripts/lib/throwaway-postgres.sh
source scripts/lib/load-sample.sh

load_sample "$TEST_DB_NAME"

export POSTGRES_HOST=127.0.0.1
export POSTGRES_PORT="$TEST_DB_PORT"
export WAREHOUSE_DB=warehouse
export TRANSFORMER_PASSWORD="$TEST_DB_PASSWORD"

cd transform
"$DBT" build "$@"
