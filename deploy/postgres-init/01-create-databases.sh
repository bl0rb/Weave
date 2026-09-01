#!/bin/sh
# Runs once, automatically, the FIRST time the postgres container starts
# against an empty $PGDATA (the official postgres/pgvector image executes
# every *.sh/*.sql file under /docker-entrypoint-initdb.d/, in lexical
# order, but ONLY on a brand-new data directory -- re-running this compose
# stack against an existing `pgdata` volume will NOT re-apply it; see
# deploy/README.md's "Database initialization" section for how to run it
# by hand against an already-initialized cluster).
#
# Creates the two additional per-service databases ADR-0004 calls for
# (`weave_ingest` itself already exists -- it is $POSTGRES_DB, created by
# the base image before this script runs) plus the read-only role
# ADR-0005 / contracts/chunk-store.md's "DB-Rolle" section
# specifies for Weave-Retrieval's shared, read-only access to
# `weave_knowledge`.
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE DATABASE weave_knowledge;
    CREATE DATABASE weave_api;
EOSQL

# weave_retrieval_ro: LOGIN + CONNECT + SELECT only, exactly the grants in
# chunk-store.md's "DB-Rolle" section -- reproduced here so a fresh stack
# has read access wired up without a manual psql session. Note this runs
# BEFORE Weave-Knowledge's own Alembic migrations ever create `documents`/
# `chunks`/`collections` (those containers haven't even started yet at
# this point in `docker compose up`), so there is nothing to GRANT SELECT
# on YET -- the ALTER DEFAULT PRIVILEGES statement below is what actually
# matters here: it makes every table Alembic (running as $POSTGRES_USER)
# creates in this database from now on automatically SELECT-able by
# weave_retrieval_ro, with no further manual GRANT needed per migration.
#
# RETRIEVAL_DB_PASSWORD comes from this container's own environment (see
# the postgres service's `environment:` block in docker-compose.weave.yml
# -- set once, alongside POSTGRES_PASSWORD, in deploy/.env). Plain
# variable interpolation into SQL text, same level of rigor as the rest of
# this quick-start skeleton -- do not put a password containing a single
# quote in RETRIEVAL_DB_PASSWORD.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "weave_knowledge" <<-EOSQL
    CREATE ROLE weave_retrieval_ro WITH LOGIN PASSWORD '${RETRIEVAL_DB_PASSWORD}';
    GRANT CONNECT ON DATABASE weave_knowledge TO weave_retrieval_ro;
    GRANT USAGE ON SCHEMA public TO weave_retrieval_ro;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO weave_retrieval_ro;
EOSQL

echo "01-create-databases.sh: weave_knowledge + weave_api created, weave_retrieval_ro role granted default SELECT privileges."
