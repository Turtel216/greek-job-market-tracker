#!/usr/bin/env bash
# Creates the login roles and the application databases.
# The postgres image runs this once, on the first start with an empty volume.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    -v loader_password="$LOADER_PASSWORD" \
    -v transformer_password="$TRANSFORMER_PASSWORD" \
    -v bi_reader_password="$BI_READER_PASSWORD" \
    -v airflow_password="$AIRFLOW_DB_PASSWORD" \
    -v metabase_password="$METABASE_DB_PASSWORD" <<'SQL'
-- Warehouse roles, see "Least-privilege database roles" in the design doc
create role loader login password :'loader_password';
create role transformer login password :'transformer_password';
create role bi_reader login password :'bi_reader_password';

-- Application roles, each owns only its own database
create role airflow login password :'airflow_password';
create role metabase_app login password :'metabase_password';

create database airflow owner airflow;
create database metabase_app owner metabase_app;

revoke all on database airflow from public;
revoke all on database metabase_app from public;
SQL
