#!/usr/bin/env bash
# notify-run.sh — run-ticket.sh + Slack reporting (SFP-133 glue; best-effort).
# Usage: ./scripts/notify-run.sh SFP-XXX [--slug x] [--resume]
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
set -a; . "$REPO/.env"; set +a
exec uv run --quiet python "$REPO/scripts/notify_slack.py" "$@"
