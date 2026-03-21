# Starts a throwaway Postgres for tests. Source this file from a script that
# runs from the repo root.
#
# Uses the same image as Compose with the init scripts from
# infra/postgres/init, so tests see the real tables, roles and grants. The
# container is removed when the calling script exits. Afterwards
# TEST_DB_PORT holds its port on 127.0.0.1, and every role has the password
# in TEST_DB_PASSWORD.

TEST_DB_IMAGE=$(grep -m1 -oE 'image: postgres:[^[:space:]]+' docker-compose.yml | cut -d' ' -f2)
TEST_DB_NAME="jobtracker-test-db-$$"
TEST_DB_PASSWORD=test

docker run -d --rm --name "$TEST_DB_NAME" -p 127.0.0.1::5432 \
    -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD="$TEST_DB_PASSWORD" -e POSTGRES_DB=warehouse \
    -e LOADER_PASSWORD="$TEST_DB_PASSWORD" -e TRANSFORMER_PASSWORD="$TEST_DB_PASSWORD" \
    -e BI_READER_PASSWORD="$TEST_DB_PASSWORD" -e AIRFLOW_DB_PASSWORD="$TEST_DB_PASSWORD" \
    -e METABASE_DB_PASSWORD="$TEST_DB_PASSWORD" \
    -v "$PWD/infra/postgres/init:/docker-entrypoint-initdb.d:ro" \
    "$TEST_DB_IMAGE" >/dev/null
trap 'docker stop "$TEST_DB_NAME" >/dev/null' EXIT

# The image restarts the server once the init scripts are done, so wait
# for that message and not just for the first time it accepts connections
for _ in $(seq 60); do
    if docker logs "$TEST_DB_NAME" 2>&1 | grep -q "init process complete" \
        && docker exec "$TEST_DB_NAME" pg_isready -h 127.0.0.1 -U postgres -d warehouse >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

TEST_DB_PORT=$(docker port "$TEST_DB_NAME" 5432/tcp | head -1 | cut -d: -f2)
