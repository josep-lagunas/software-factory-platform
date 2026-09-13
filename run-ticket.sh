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
#   RUN_TICKET_ALLOW_STALE_MAIN=1 ./run-ticket.sh SFP-232  # skip the local-main
#                                                 # freshness guard (offline /
#                                                 # intentionally pinned main)
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
# Local-main freshness (SFP-249 lesson, measured 2026-09-06 on SFP-243): this
# script executes the pipeline FROM THE LOCAL REPO — a lagging local main means
# the run itself executes pre-fix code (the 243 run posted a bare-word review
# body 10h AFTER the SFP-249 rationale fix landed on origin/main, because
# local main was stale). Squash merges never advance local main, so EVERY
# merge via gh/adapter leaves the repo behind. Fail loudly instead of running
# old code silently. Skippable explicitly (RUN_TICKET_ALLOW_STALE_MAIN=1) for
# offline/intentional-pin launches.
# NOTE: this compares the main COMMIT only — uncommitted local changes (the
# standing smoke patches) are deliberately out of scope; they layer on top of
# whatever main is, and the operator owns them explicitly.
# ---------------------------------------------------------------------------
if [[ "${RUN_TICKET_ALLOW_STALE_MAIN:-0}" != "1" ]] && git -C "$REPO_ROOT" rev-parse --git-dir >/dev/null 2>&1; then
  git -C "$REPO_ROOT" fetch origin main --quiet || {
    echo "warning: could not fetch origin/main (offline?) — proceeding unverified" >&2
  }
  LOCAL_MAIN="$(git -C "$REPO_ROOT" rev-parse main 2>/dev/null || true)"
  REMOTE_MAIN="$(git -C "$REPO_ROOT" rev-parse origin/main 2>/dev/null || true)"
  if [[ -n "$LOCAL_MAIN" && -n "$REMOTE_MAIN" && "$LOCAL_MAIN" != "$REMOTE_MAIN" ]]; then
    echo "error: local main ($LOCAL_MAIN) is STALE vs origin/main ($REMOTE_MAIN)." >&2
    echo "       The pipeline would execute pre-merge code. Run:" >&2
    echo "         git -C $REPO_ROOT pull --rebase --autostash origin main" >&2
    echo "       (autostash preserves uncommitted smoke patches; then relaunch)." >&2
    echo "       To launch anyway: RUN_TICKET_ALLOW_STALE_MAIN=1 $0 ..." >&2
    exit 67
  fi
  echo "[run-ticket] local main fresh vs origin/main ($LOCAL_MAIN)" >&2
fi

# ---------------------------------------------------------------------------
# Env bridge: .env (unprefixed) -> SFP_-prefixed settings the composition root
# reads. Each is overridable from the caller's environment.
# ---------------------------------------------------------------------------
: "${SFP_ANTHROPIC_BASE_URL:="$ANTHROPIC_BASE_URL"}"
# Model tiers aligned with the orchestrator harness (.claude/agents/sfp-*.md
# frontmatter, SFP-237). Only planner/coder/reviewer carry per-role overrides
# (ROLES_WITH_OVERRIDE in model_config.py); test_designer/readiness/other roles
# resolve to default_model — the floor. So:
#   - default_model = glm-5.3  -> floor for test_designer + readiness.
#   - planner + reviewer stay on the non-flash tier (SFP-79 precedent: weak
#     reviewer tier = worst dogfood outcome; flash is coder-only).
#   - coder = glm-5.3-flash + effort low (SFP-255, the 2026-09-13 A/B on
#     SFP-134/135/136: 24 vs 58 turns, 358s vs 1194s, 2.05M vs 5.28M tokens,
#     quality gates equal or better — decision rule "gates equal AND turns+time
#     lower" met decisively). Fallback on regression: override to a non-flash id
#     (served as GLM-5.3) + drop the effort override.
# NOTE on labels: z.ai's GLM Coding Plan serves glm-5.1/glm-5.2 requests as
# GLM-5.3 (aliases; plan metering confirms — all non-flash usage buckets as
# GLM-5.3). The glm-5.3 names below are the honest as-routed labels so
# usage-metrics lines stop misreporting the served model.
# Each var is overridable from the caller's environment (SFP_AGENT_MODEL_* or the
# convenience MODEL_*); the ${VAR:-default} nesting makes caller env always win.
: "${SFP_DEFAULT_MODEL:="glm-5.3"}"                            # floor for test_designer/readiness
: "${SFP_AGENT_MODEL_CODER:="${MODEL_CODER:-glm-5.3-flash}"}"  # Coder on flash (SFP-255)
: "${SFP_AGENT_MODEL_PLANNER:="${MODEL_PLANNER:-glm-5.3}"}"    # Planner on the flagship tier
: "${SFP_AGENT_MODEL_REVIEWER:="${MODEL_REVIEWER:-glm-5.3}"}"  # Reviewer on the flagship tier
# Coder reasoning effort (SFP-255): low wins on the A/B population (mechanical
# N=3 incl. two substantive COMM tickets). Remove this line to fall back to the
# committed settings.py default (medium).
: "${SFP_CODER_EFFORT:="low"}"
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
# DURABLE worktree base (2026-09-07, second /tmp-decay incident measured — the
# clone cache's .git was corrupted by macOS /tmp cleanup mid-audit and nothing
# was lost only because everything is derivable from origin; the first incident
# ate SFP-148's worktree+checkpoints on 2026-09-03). ~/Library/Caches survives
# reboots and OS cleanup; sfp-worktrees is fully reclaimable (re-clone/re-cut)
# so Caches (not Application Support) is the right tier. Overridable as before.
: "${SFP_WORKTREE_BASE:="$HOME/Library/Caches/sfp-worktrees"}"
# Stream liveness watchdogs (SFP-242). NOT exported here — these are comments
# only; the settings defaults apply unless an operator overrides them. The SDK
# query() spawns the Claude Code CLI; when the endpoint goes mute the CLI stays
# up and the run used to hang for hours with zero stream events. Two budgets
# bound that (WorkspaceWorkerSettings, env-tunable, SFP_ prefix):
#   SFP_SPAWN_FIRST_EVENT_TIMEOUT   budget (s) for the FIRST stream event
#                                   after spawn. Default 300 (5 min).
#   SFP_SPAWN_PROGRESS_TIMEOUT      max INACTIVITY (s) between consecutive
#                                   stream events once the first arrived.
#                                   Default 900 (15 min).
# A trip terminates the CLI and raises the existing transient error (retried /
# failed closed by the existing path). Raise these for a known-slow cold start
# (a 5-min first event can legitimately happen on an unusually slow endpoint);
# lower them to abort sooner. To override, set them in the caller's env or .env,
# e.g. SFP_SPAWN_FIRST_EVENT_TIMEOUT=600 ./run-ticket.sh
# NOTE: a total run LONGER than both budgets is fine — only INACTIVITY trips.

export SFP_ANTHROPIC_BASE_URL SFP_DEFAULT_MODEL SFP_AGENT_MODEL_CODER \
       SFP_LLM_PROVIDER_SECRET_REF SFP_JIRA_EMAIL SFP_JIRA_SITE \
       SFP_GIT_OWNER SFP_GIT_REPO SFP_WORKTREE_BASE
# Per-role overrides (always set via the ${VAR:-default} defaults above; guard
# kept defensive — empty would crash the model_config gate).
[[ -n "${SFP_AGENT_MODEL_PLANNER:-}" ]] && export SFP_AGENT_MODEL_PLANNER
[[ -n "${SFP_AGENT_MODEL_REVIEWER:-}" ]] && export SFP_AGENT_MODEL_REVIEWER
[[ -n "${SFP_CODER_EFFORT:-}" ]] && export SFP_CODER_EFFORT
# Secrets the LocalSecretProvider resolves by name (kept unprefixed, as in .env):
export ANTHROPIC_AUTH_TOKEN JIRA_API_TOKEN GITHUB_TOKEN_CODER GITHUB_TOKEN_REVIEWER

# Echo the resolved config as KEY=set/(unset), never the values — secrets stay hidden.
cfg_keys=(SFP_ANTHROPIC_BASE_URL SFP_DEFAULT_MODEL SFP_AGENT_MODEL_CODER \
          SFP_AGENT_MODEL_PLANNER SFP_AGENT_MODEL_REVIEWER SFP_CODER_EFFORT \
          SFP_JIRA_SITE \
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
