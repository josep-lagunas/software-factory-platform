#!/usr/bin/env bash
# run-ticket.sh — launch one ticket through the SFP vertical slice (ticket_pipeline).
#
# This is an OPERATIONAL wrapper, not application code: it bridges the unprefixed
# keys in `.env` into the `SFP_`-prefixed settings the composition root reads
# (WorkspaceWorkerSettings uses env_prefix="SFP_"), pins the real repo
# coordinates, and scrubs GitHub PATs from the log. It exists so the launch
# recipe (re-derived each session after run-sfp227.sh was deleted) is not lost.
#
# Usage:
#   ./run-ticket.sh SFP-232                       # run ticket SFP-232
#   ./run-ticket.sh SFP-232 --slug calibrate-rubric
#   ./run-ticket.sh SFP-232 --resume              # resume from stage checkpoints
#   TICKET=SFP-232 ./run-ticket.sh --resume       # ticket via env
#   MODEL_CODER=glm-5.2 ./run-ticket.sh SFP-232   # per-role model override
#
# All unrecognized args are forwarded to ticket_pipeline (e.g. --base-branch).
#
# SECURITY: the slice spawns a Claude CLI that needs ANTHROPIC_* to reach GLM.
# If you launch this FROM a Claude Code session, the bash token-sandbox would
# strip those vars (the sfp-token-sandbox-redaction trap) — run it UNSANDBOXED
# (e.g. `! ./run-ticket.sh SFP-232` or approve the unsandboxed run). From a real
# shell this does not apply. Clone-error tracebacks print the PAT in the clone
# URL, so stdout/stderr are scrubbed (ghp_… → ghp_REDACTED) before logging.
#
# Grounded in: sfp-slice-launch-env-mapping (the 3 launch gaps), sfp-id019-runtime
# -validated (endpoint/recipe). Reusable across sessions.

set -euo pipefail

# ---------------------------------------------------------------------------
# Args: first positional (or $TICKET env) is the ticket; the rest forward
# through. Only consume $1 when it was actually used as the ticket — if $TICKET
# came from the environment, $1 is the first pipeline arg and must NOT shift.
# ---------------------------------------------------------------------------
TICKET_FROM_ARG=0
if [[ -z "${TICKET:-}" && -n "${1:-}" ]]; then
  TICKET="$1"
  TICKET_FROM_ARG=1
fi
if [[ -z "${TICKET:-}" ]]; then
  echo "usage: $0 <SFP-XXX> [--slug <slug>] [--resume] [--base-branch <branch>]" >&2
  echo "       (or: TICKET=SFP-XXX $0 [--resume])" >&2
  exit 64
fi
# Drop the ticket positional only if we consumed $1 as it; "$@" is then pure
# pipeline args, re-added explicitly via --ticket below.
[[ "$TICKET_FROM_ARG" -eq 1 ]] && shift

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

# ---------------------------------------------------------------------------
# Load .env (unprefixed secrets + endpoint). Fail loud if absent — no silent
# fallback to wrong/empty creds.
# ---------------------------------------------------------------------------
if [[ -f "$REPO_ROOT/.env" ]]; then
  # shellcheck disable=SC1091
  set -a
  . "$REPO_ROOT/.env"
  set +a
else
  echo "error: $REPO_ROOT/.env not found (expected JIRA_*, ANTHROPIC_*, GITHUB_TOKEN_* )" >&2
  exit 65
fi

# ---------------------------------------------------------------------------
# Env bridge: .env (unprefixed) -> SFP_-prefixed settings the composition root
# reads. Each is overridable from the caller's environment.
# ---------------------------------------------------------------------------
: "${SFP_ANTHROPIC_BASE_URL:="$ANTHROPIC_BASE_URL"}"
: "${SFP_DEFAULT_MODEL:="glm-4.6"}"                       # floor for emit-JSON agents
: "${SFP_AGENT_MODEL_CODER:="${MODEL_CODER:-glm-5.2}"}"   # Coder on the capable tier
# Planner/Reviewer overrides are OPTIONAL: AgentModelConfig rejects empty/
# whitespace strings (model_config.py _check_non_empty), so only set them when a
# value is actually provided (an existing SFP_AGENT_MODEL_* or the convenience
# MODEL_PLANNER/MODEL_REVIEWER). Unset => os.environ.get returns None => optional.
if [[ -z "${SFP_AGENT_MODEL_PLANNER:-}" && -n "${MODEL_PLANNER:-}" ]]; then
  SFP_AGENT_MODEL_PLANNER="$MODEL_PLANNER"
fi
if [[ -z "${SFP_AGENT_MODEL_REVIEWER:-}" && -n "${MODEL_REVIEWER:-}" ]]; then
  SFP_AGENT_MODEL_REVIEWER="$MODEL_REVIEWER"
fi
# SecretRef is a pydantic model (extra="forbid", field `name`) -> the env value
# is parsed as JSON; a bare name raises SettingsError. MUST be JSON. The literal
# is held in single quotes (so its inner double-quotes are data, not delimiters)
# then substituted into the :- default inside the outer double-quoted expansion.
SECRET_REF_JSON='{"name":"ANTHROPIC_AUTH_TOKEN"}'
: "${SFP_LLM_PROVIDER_SECRET_REF:=$SECRET_REF_JSON}"
# Jira: .env carries JIRA_EMAIL/JIRA_SITE; build() reads SFP_JIRA_EMAIL (default
# "" -> invalid basic auth -> HTTP 404 that looks like a missing ticket).
: "${SFP_JIRA_EMAIL:="$JIRA_EMAIL"}"
: "${SFP_JIRA_SITE:="$JIRA_SITE"}"
# Repo coords: build() defaults owner=arconta/repo=sfp which 404s (the tokens
# authenticate as sfp-coder-bot/sfp-reviewer-bot but cannot SEE arconta/sfp).
: "${SFP_GIT_OWNER:="josep-lagunas"}"
: "${SFP_GIT_REPO:="software-factory-platform"}"
: "${SFP_WORKTREE_BASE:="/tmp/sfp-worktrees"}"

export SFP_ANTHROPIC_BASE_URL SFP_DEFAULT_MODEL SFP_AGENT_MODEL_CODER \
       SFP_LLM_PROVIDER_SECRET_REF SFP_JIRA_EMAIL SFP_JIRA_SITE \
       SFP_GIT_OWNER SFP_GIT_REPO SFP_WORKTREE_BASE
# Optional per-role overrides: export only when set (empty would crash the gate).
[[ -n "${SFP_AGENT_MODEL_PLANNER:-}" ]] && export SFP_AGENT_MODEL_PLANNER
[[ -n "${SFP_AGENT_MODEL_REVIEWER:-}" ]] && export SFP_AGENT_MODEL_REVIEWER
# Secrets the LocalSecretProvider resolves by name (kept unprefixed, as in .env):
export ANTHROPIC_AUTH_TOKEN JIRA_API_TOKEN GITHUB_TOKEN_CODER GITHUB_TOKEN_REVIEWER

# Echo the resolved config as KEY=set/(unset), never the values — secrets stay hidden.
cfg_keys=(SFP_ANTHROPIC_BASE_URL SFP_DEFAULT_MODEL SFP_AGENT_MODEL_CODER \
          SFP_AGENT_MODEL_PLANNER SFP_AGENT_MODEL_REVIEWER SFP_JIRA_SITE \
          SFP_GIT_OWNER SFP_GIT_REPO SFP_WORKTREE_BASE \
          SFP_JIRA_EMAIL ANTHROPIC_AUTH_TOKEN JIRA_API_TOKEN \
          GITHUB_TOKEN_CODER GITHUB_TOKEN_REVIEWER)
for k in "${cfg_keys[@]}"; do
  if [[ -n "${!k:-}" ]]; then echo "[run-ticket] $k=set" >&2; else echo "[run-ticket] $k=(unset)" >&2; fi
done

mkdir -p "$REPO_ROOT/logs"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG="$REPO_ROOT/logs/run-${TICKET}-${STAMP}.log"

echo "[run-ticket] ticket=$TICKET repo=${SFP_GIT_OWNER}/${SFP_GIT_REPO} log=$LOG" >&2

# ---------------------------------------------------------------------------
# Run the slice. uv resolves the workspace member from the repo root. PATs are
# scrubbed from both the console and the log (clone-error tracebacks leak them).
# ---------------------------------------------------------------------------
scrub() {
  sed -E 's#(ghp_|github_pat_)[A-Za-z0-9_]+#\1REDACTED#g'
}

set +e
uv run python -m workspace_worker.entrypoints.ticket_pipeline \
  --ticket "$TICKET" "$@" 2>&1 | scrub | tee "$LOG"
RC=${PIPESTATUS[0]}
set -e

echo "[run-ticket] exit=$RC log=$LOG" >&2
exit "$RC"
