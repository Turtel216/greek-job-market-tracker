#!/usr/bin/env bash
# Restores one dump into a scratch database and checks that it holds data,
# to prove the backups can be restored. The live databases are never
# touched. Every command runs in the postgres container, which sees
# BACKUP_DIR as /backups.
#
# Usage: scripts/restore.sh [dump]
#   dump  a dump under BACKUP_DIR, such as metabase_app/metabase_app-2026-11-28.dump.
#         The default is the newest warehouse dump.
#
# The scratch database is restore_<database>, such as restore_warehouse.
# It is dropped at the end unless KEEP=1, for example to recover from it.
# Set PG_EXEC to run the commands in another Postgres container.
set -euo pipefail

cd "$(dirname "$0")/.."
COMPOSE=${COMPOSE:-docker compose}
read -r -a PG_EXEC <<<"${PG_EXEC:-$COMPOSE exec -T postgres}"
KEEP=${KEEP:-0}

# Runs the shell script in $1 in the container, with the rest as its
# arguments. The script reads the admin user from $POSTGRES_USER there.
in_postgres() {
    "${PG_EXEC[@]}" sh -c "$1" sh "${@:2}"
}

DUMP=${1:-}
if [ -z "$DUMP" ]; then
    # The warehouse is POSTGRES_DB in the container, and its dumps are in a
    # folder of the same name
    # shellcheck disable=SC2016
    DUMP=$(in_postgres '
        cd "/backups/$POSTGRES_DB" 2>/dev/null || exit 0
        ls -1 | grep -E "^$POSTGRES_DB-[0-9]{4}-[0-9]{2}-[0-9]{2}\.dump$" | sort | tail -n 1 \
            | sed "s|^|$POSTGRES_DB/|"
    ')
    if [ -z "$DUMP" ]; then
        echo "No warehouse dump found in BACKUP_DIR. Run make backup first." >&2
        exit 1
    fi
fi

NAME=$(basename "$DUMP")
if ! [[ "$NAME" =~ ^([a-z0-9_]+)-[0-9]{4}-[0-9]{2}-[0-9]{2}\.dump$ ]] || [[ "$DUMP" == *..* ]] \
    || [[ "$DUMP" == /* ]]; then
    echo "Expected a dump under BACKUP_DIR such as warehouse/warehouse-2026-11-28.dump, got $DUMP" >&2
    exit 1
fi
DATABASE=${BASH_REMATCH[1]}
SCRATCH="restore_$DATABASE"

if ! in_postgres 'test -f "/backups/$1"' "$DUMP"; then
    echo "No dump at $DUMP in BACKUP_DIR" >&2
    exit 1
fi

drop_scratch() {
    # shellcheck disable=SC2016
    in_postgres 'dropdb -U "$POSTGRES_USER" --if-exists "$1"' "$SCRATCH"
}
if [ "$KEEP" != 1 ]; then
    trap drop_scratch EXIT
fi

echo "Restoring $DUMP into $SCRATCH"
# shellcheck disable=SC2016
in_postgres '
    set -e
    dropdb -U "$POSTGRES_USER" --if-exists "$1"
    createdb -U "$POSTGRES_USER" "$1"
    pg_restore -U "$POSTGRES_USER" --dbname "$1" --exit-on-error --single-transaction "/backups/$2"
' "$SCRATCH" "$DUMP"

# Fails when nothing came back, then shows the rows in the tables that
# matter, from whichever of the two databases this is
# shellcheck disable=SC2016
in_postgres 'psql -X -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$1" -f -' "$SCRATCH" <<'SQL'
\pset footer off
do $$
begin
    if not exists (
        select from information_schema.tables
        where table_schema not in ('pg_catalog', 'information_schema')
    ) then
        raise exception 'the restored database has no tables';
    end if;
end
$$;

select table_schema as schema, count(*) as tables
from information_schema.tables
where table_schema not in ('pg_catalog', 'information_schema')
group by table_schema
order by table_schema;

select string_agg(
    format('select %L as "table", count(*) as "rows" from %s', t, t), ' union all '
)
from unnest(array[
    'raw.raw_postings', 'ops.ingest_runs', 'ops.posting_fingerprints', 'ops.dbt_runs',
    'marts.dim_jobs', 'public.report_dashboard', 'public.report_card', 'public.core_user'
]) as t
where to_regclass(t) is not null
\gexec

select format('select max(run_date) as newest_run_date from %s', t)
from unnest(array['raw.raw_postings']) as t
where to_regclass(t) is not null
\gexec
SQL

if [ "$KEEP" = 1 ]; then
    echo "Restore check passed. Kept the restored database as $SCRATCH."
else
    echo "Restore check passed. Dropped $SCRATCH."
fi
echo "Log the date and the dump in the restore log in docs/operations.md."
