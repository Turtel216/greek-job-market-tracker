#!/usr/bin/env bash
# Runs the loader's Postgres tests against a throwaway database.
#
# Starts the same Postgres image as Compose with the init scripts from
# infra/postgres/init, so the tests see the real tables and grants, then
# removes the container again. Extra arguments go to pytest.
set -euo pipefail

cd "$(dirname "$0")/.."

IMAGE=$(grep -m1 -oE 'image: postgres:[^[:space:]]+' docker-compose.yml | cut -d' ' -f2)
NAME="jobtracker-test-db-$$"
PASSWORD=test

docker run -d --rm --name "$NAME" -p 127.0.0.1::5432 \
    -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD="$PASSWORD" -e POSTGRES_DB=warehouse \
    -e LOADER_PASSWORD="$PASSWORD" -e TRANSFORMER_PASSWORD="$PASSWORD" \
    -e BI_READER_PASSWORD="$PASSWORD" -e AIRFLOW_DB_PASSWORD="$PASSWORD" \
    -e METABASE_DB_PASSWORD="$PASSWORD" \
    -v "$PWD/infra/postgres/init:/docker-entrypoint-initdb.d:ro" \
    "$IMAGE" >/dev/null
trap 'docker stop "$NAME" >/dev/null' EXIT

# The image restarts the server once the init scripts are done, so wait
# for that message and not just for the first time it accepts connections
for _ in $(seq 60); do
    if docker logs "$NAME" 2>&1 | grep -q "init process complete" \
        && docker exec "$NAME" pg_isready -h 127.0.0.1 -U postgres -d warehouse >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

PORT=$(docker port "$NAME" 5432/tcp | head -1 | cut -d: -f2)
export JOBTRACKER_TEST_ADMIN_DSN="postgresql://postgres:$PASSWORD@127.0.0.1:$PORT/warehouse"
export JOBTRACKER_TEST_LOADER_DSN="postgresql://loader:$PASSWORD@127.0.0.1:$PORT/warehouse"

cd ingestion
uv run pytest -m postgres "$@"
