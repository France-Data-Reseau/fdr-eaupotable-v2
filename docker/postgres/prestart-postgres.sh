#!/usr/bin/env bash
set -euo pipefail

CERT_DIR="/var/lib/postgresql/tls"
CERT_FILE="${CERT_DIR}/server.crt"
KEY_FILE="${CERT_DIR}/server.key"

mkdir -p "${CERT_DIR}"
chown postgres:postgres "${CERT_DIR}"

if [[ ! -f "${CERT_FILE}" || ! -f "${KEY_FILE}" ]]; then
  if ! command -v openssl >/dev/null 2>&1; then
    echo "openssl is required to generate PostgreSQL TLS certificates." >&2
    exit 1
  fi

  echo "Generating self-signed TLS certificate for PostgreSQL..."
  openssl req -new -x509 -nodes \
    -days "${POSTGRES_TLS_DAYS:-3650}" \
    -subj "/CN=${POSTGRES_TLS_CN:-db}" \
    -keyout "${KEY_FILE}" \
    -out "${CERT_FILE}"

  chmod 600 "${KEY_FILE}"
  chmod 644 "${CERT_FILE}"
  chown postgres:postgres "${KEY_FILE}" "${CERT_FILE}"
fi

exec /usr/local/bin/docker-entrypoint.sh "$@"
