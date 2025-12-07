#!/usr/bin/env bash
# Sets up the warehouse database: extensions, schemas and grants.
# The postgres image runs this once, on the first start with an empty volume.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    -v db="$POSTGRES_DB" <<'SQL'
create extension if not exists pg_trgm;
create extension if not exists unaccent;

revoke all on database :"db" from public;
grant connect on database :"db" to loader, transformer, bi_reader;

-- raw and ops are written by the loader. Their tables are created by the
-- admin role, so the default privileges below apply to them.
create schema raw;
create schema ops;

grant usage on schema raw to loader, transformer;
grant usage on schema ops to loader, transformer, bi_reader;

alter default privileges in schema raw grant insert on tables to loader;
alter default privileges in schema raw grant select on tables to transformer;

alter default privileges in schema ops grant select, insert, update on tables to loader;
alter default privileges in schema ops grant select on tables to transformer, bi_reader;

-- dbt owns the schemas it builds
create schema staging authorization transformer;
create schema snapshots authorization transformer;
create schema intermediate authorization transformer;
create schema marts authorization transformer;
create schema qa authorization transformer;

-- Metabase reads marts and qa only
grant usage on schema marts, qa to bi_reader;
alter default privileges for role transformer in schema marts grant select on tables to bi_reader;
alter default privileges for role transformer in schema qa grant select on tables to bi_reader;
SQL
