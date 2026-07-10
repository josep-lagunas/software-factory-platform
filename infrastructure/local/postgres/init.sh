#!/usr/bin/env bash
# Provision the per-service logical databases in the single Postgres cluster.
#
# Mounted at /docker-entrypoint-initdb.d/init.sh and executed exactly once by the
# official postgres image's entrypoint — only on first initialization of an empty
# data volume (so it does NOT re-run after a plain `down`/`up`, only after
# `down -v` wipes the volume).
#
# MAS §10.14: one cluster hosts multiple logical databases; infrastructure may be
# shared but ownership is not. In local dev the services connect as the cluster
# superuser ($POSTGRES_USER) for simplicity; the logical-DB boundary still keeps
# each service's data isolated.
#
# The `\gexec` guard makes each CREATE idempotent (safe to re-run manually).

set -euo pipefail

# One logical database per service boundary.
DATABASES=(
  "identity"
  "orchestrator"
  "communication"
  "external_events"
)

for db in "${DATABASES[@]}"; do
  echo "Initializing logical database: ${db}"
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<EOSQL
SELECT 'CREATE DATABASE ${db}'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '${db}')\gexec
EOSQL
done

echo "All logical databases ready: ${DATABASES[*]}"
