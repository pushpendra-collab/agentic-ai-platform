#!/bin/sh
# InvenLogic TEST replica (docker/erp-test/README.md): three of the six service databases,
# their owners (as Flyway would leave them) and the agent's read-only role. TEST only.
set -eu

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<SQL
CREATE ROLE master_data_owner NOLOGIN;
CREATE ROLE sales_owner NOLOGIN;
CREATE ROLE inventory_owner NOLOGIN;
CREATE ROLE agentic_erp_reader LOGIN PASSWORD '${ERP_READER_PASSWORD}';
ALTER ROLE agentic_erp_reader SET default_transaction_read_only = on;
CREATE DATABASE master_data_db OWNER master_data_owner;
CREATE DATABASE sales_db OWNER sales_owner;
CREATE DATABASE inventory_db OWNER inventory_owner;
SQL

for db in master_data_db sales_db inventory_db; do
    psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$db" <<SQL
REVOKE ALL ON DATABASE $db FROM PUBLIC;
GRANT CONNECT ON DATABASE $db TO agentic_erp_reader;
GRANT USAGE ON SCHEMA public TO agentic_erp_reader;
SQL
    psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$db" -f "/erp-sql/$db.sql"
done
