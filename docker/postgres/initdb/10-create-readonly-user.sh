#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${POSTGRES_READONLY_USER:-}" || -z "${POSTGRES_READONLY_PASSWORD:-}" ]]; then
  echo "POSTGRES_READONLY_USER or POSTGRES_READONLY_PASSWORD is empty. Skipping readonly role creation."
  exit 0
fi

DB_NAME="${POSTGRES_READONLY_DB:-${POSTGRES_DB}}"

echo "Ensuring readonly role ${POSTGRES_READONLY_USER} on database ${DB_NAME}..."

psql -v ON_ERROR_STOP=1 \
  --username "${POSTGRES_USER}" \
  --dbname "${DB_NAME}" \
  -v ro_user="${POSTGRES_READONLY_USER}" \
  -v ro_password="${POSTGRES_READONLY_PASSWORD}" \
  -v ro_db="${DB_NAME}" <<'SQL'
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'ro_user', :'ro_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'ro_user')\gexec

SELECT format('ALTER ROLE %I WITH PASSWORD %L', :'ro_user', :'ro_password')\gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO %I', :'ro_db', :'ro_user')\gexec

SELECT format('GRANT USAGE ON SCHEMA public TO %I', :'ro_user')\gexec
SELECT format('GRANT SELECT ON ALL TABLES IN SCHEMA public TO %I', :'ro_user')\gexec
SELECT format('GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO %I', :'ro_user')\gexec

SELECT format('ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO %I', :'ro_user')\gexec
SELECT format('ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON SEQUENCES TO %I', :'ro_user')\gexec
SQL
