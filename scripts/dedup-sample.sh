#!/usr/bin/env bash
# Scores deduplication on the made-up postings in tests/dedup against the
# labels in transform/seeds/dedup_labels.csv, using a throwaway database.
# Fails when precision is under 0.95. Set DBT to use another dbt binary.
# scripts/lib/load-sample.sh says how the sample is loaded.
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
"$DBT" build --exclude-resource-type unit_test --quiet
"$DBT" show --select dedup_precision --output json --quiet | python3 -c '
import json, sys

scores = json.load(sys.stdin)["show"][0]
for name, value in scores.items():
    print(f"{name:>18}: {value}")
if scores["precision_score"] is None or scores["precision_score"] < 0.95:
    sys.exit("Precision is under 0.95")
'
