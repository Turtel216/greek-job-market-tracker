#!/usr/bin/env bash
# Creates the backup role that the nightly pg_dump connects as. It can read
# every table in the warehouse and metabase_app databases and write nothing.
# The postgres image runs this once, on the first start with an empty
# volume. It is safe to run again, so `make db-backup-role` adds the role to
# a database that already exists.
set -euo pipefail

if [ -z "${BACKUP_PASSWORD:-}" ]; then
    echo "BACKUP_PASSWORD is not set, so the backup role was not created" >&2
    exit 0
fi

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    -v db="$POSTGRES_DB" \
    -v backup_password="$BACKUP_PASSWORD" <<'SQL'
select 'create role backup login'
where not exists (select from pg_roles where rolname = 'backup')
\gexec
alter role backup login password :'backup_password';

-- Read access to every table, view and sequence, and nothing else
grant pg_read_all_data to backup;

-- Both databases revoke connect from public
grant connect on database :"db" to backup;
grant connect on database metabase_app to backup;
SQL
