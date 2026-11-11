#!/usr/bin/env bash
# Dumps the warehouse and metabase_app databases now, the same way the
# nightly jobtracker_maintenance DAG does. It runs `jobtracker backup` in
# the running Airflow scheduler, so the dumps land in BACKUP_DIR (./backups
# by default) and old ones are pruned. A second backup on the same day
# replaces that day's dump.
set -euo pipefail

cd "$(dirname "$0")/.."
COMPOSE=${COMPOSE:-docker compose}
RUN_DATE=$(TZ=Europe/Athens date +%F)

# The warehouse name is read inside the container, where Compose set it
# shellcheck disable=SC2016
$COMPOSE exec -T airflow-scheduler sh -c '
    set -e
    for database in "$WAREHOUSE_DB" metabase_app; do
        jobtracker backup --database "$database" --run-date "$1" --folder /opt/airflow/backups
    done
' sh "$RUN_DATE"
