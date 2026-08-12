#!/usr/bin/env bash
# Local init container entrypoint (SFP-79).
#
# Orchestrates the deterministic provisioning order required by the PRSpec
# (MAS §12.7 — NEVER parallel):
#
#   1. Wait for Postgres + LocalStack readiness (readiness.py utilities);
#   2. pulumi up against LocalStack (run_pulumi.py) — SNS/SQS topics/queues;
#   3. alembic upgrade head per logical DB (run_alembic.py) — sequential.
#
# Idempotent: every step is safe to re-run on container restart. The container
# is one-shot (``restart: no``); on a hard failure the user reprovisions with
# ``docker compose up --force-recreate``.
#
# Fail-fast: ``set -euo pipefail`` aborts on the first hard failure (a real
# migration error or a pulumi-up error against a present program). Skip-and-
# continue (service with no persistence / no tables) is handled inside the
# Python runners and never reaches this shell.

set -euo pipefail

echo "step: init: awaiting dependency readiness"

python - <<'PY'
import os

from readiness import wait_for_localstack, wait_for_postgres

host = os.environ.get("POSTGRES_HOST", "postgres")
port = int(os.environ.get("POSTGRES_PORT", "5432"))
user = os.environ.get("POSTGRES_USER", "sfp")
password = os.environ.get("POSTGRES_PASSWORD", "sfp_local_dev")
# Probe the always-present maintenance DB for Postgres readiness.
maintenance_url = f"postgresql+psycopg://{user}:{password}@{host}:{port}/postgres"
wait_for_postgres(maintenance_url)
wait_for_localstack(os.environ.get("LOCALSTACK_ENDPOINT", "http://localstack:4566"))
PY

echo "step: init: dependencies ready"

python /app/run_pulumi.py
python /app/run_alembic.py

echo "step: init: provisioning complete"
