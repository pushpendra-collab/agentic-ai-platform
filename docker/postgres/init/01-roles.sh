#!/bin/bash
# Runs once, on first start of an empty data volume.
# Least privilege: the schema owner runs migrations; the application role can
# only read and write rows in tables the owner creates. It cannot CREATE, ALTER
# or DROP anything, so even a compromised application cannot change the schema.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
	CREATE ROLE ${APP_DB_USER} LOGIN PASSWORD '${APP_DB_PASSWORD}'
	    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
	CREATE DATABASE ${TEST_DB_NAME} OWNER ${POSTGRES_USER};
EOSQL

for db in "$POSTGRES_DB" "$TEST_DB_NAME"; do
	psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$db" <<-EOSQL
		REVOKE ALL ON DATABASE ${db} FROM PUBLIC;
		GRANT CONNECT, TEMPORARY ON DATABASE ${db} TO ${APP_DB_USER};
		REVOKE CREATE ON SCHEMA public FROM PUBLIC;
		GRANT USAGE ON SCHEMA public TO ${APP_DB_USER};
		ALTER DEFAULT PRIVILEGES FOR ROLE ${POSTGRES_USER} IN SCHEMA public
		    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO ${APP_DB_USER};
		ALTER DEFAULT PRIVILEGES FOR ROLE ${POSTGRES_USER} IN SCHEMA public
		    GRANT USAGE, SELECT ON SEQUENCES TO ${APP_DB_USER};
	EOSQL
done
