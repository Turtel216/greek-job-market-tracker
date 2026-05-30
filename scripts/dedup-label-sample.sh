#!/usr/bin/env bash
# Writes the pairs picked by the dedup_label_sample analysis to a CSV for
# labeling by hand. Fill in is_duplicate with true or false, then copy the
# first five columns of each labeled row into transform/seeds/dedup_labels.csv.
#
# Usage: scripts/dedup-label-sample.sh [output file]
# The default output file is dedup-label-sample.csv. dbt reads the
# connection from the environment, as in `make dbt-build`. Set DBT to use
# another dbt binary.
set -euo pipefail

OUT=$(realpath -m "${1:-dedup-label-sample.csv}")
cd "$(dirname "$0")/.."
DBT=${DBT:-$PWD/transform/.venv/bin/dbt}

cd transform
"$DBT" show --select dedup_label_sample --limit -1 --output json --quiet \
    | python3 -c '
import csv, json, sys

rows = json.load(sys.stdin)["show"]
if not rows:
    sys.exit("No pairs to label")
columns = list(rows[0])
# The column to fill in goes right after the five that make a label
columns.insert(4, "is_duplicate")
writer = csv.DictWriter(open(sys.argv[1], "w", newline=""), fieldnames=columns)
writer.writeheader()
writer.writerows(rows)
print(f"Wrote {len(rows)} pairs to {sys.argv[1]}")
' "$OUT"
