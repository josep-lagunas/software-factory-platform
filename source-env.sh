#!/usr/bin/env sh
# SFP env loader.
#
# Usage (from anywhere in the repo, incl. a git worktree):
#     source ./source-env.sh      # zsh / bash
#     . ./source-env.sh           # POSIX sh
#
# You MUST source this file — running it directly (./source-env.sh) will NOT
# export anything into your shell, because it would execute in a subshell.
#
# It loads .env with auto-export (`set -a`) so EVERY key becomes an
# environment variable inherited by child processes (python, gh, git),
# regardless of whether the line in .env carries an `export` prefix.
# (.env is mixed: Jira keys use `export`, the Anthropic + GitHub keys do not.)
#
# .env resolution (worktree-safe): an explicit SFP_ENV_FILE wins; otherwise the
# MAIN repo root's .env is used — resolved via `git rev-parse --git-common-root`
# so that a git worktree (which has no .env of its own, since .env is gitignored
# and lives only in the main checkout) still loads the real credentials. Without
# this, sourcing from a worktree silently leaves GITHUB_TOKEN_*/JIRA_* empty and
# `gh` falls back to the human's stored auth (mis-attributing PRs).
# Override with:  SFP_ENV_FILE=/path/.env source ./source-env.sh

if [ -n "${SFP_ENV_FILE:-}" ]; then
  ENV_FILE="$SFP_ENV_FILE"
else
  # --git-common-dir is the shared .git dir (absolute from a linked worktree,
  # ".git" from the main checkout). Its parent is the main repo root, where the
  # gitignored .env actually lives.
  _GIT_COMMON="$(git rev-parse --git-common-dir 2>/dev/null)"
  _REPO_ROOT=""
  [ -n "$_GIT_COMMON" ] && _REPO_ROOT="$(cd "$_GIT_COMMON/.." && pwd 2>/dev/null)"
  if [ -n "$_REPO_ROOT" ] && [ -f "$_REPO_ROOT/.env" ]; then
    ENV_FILE="$_REPO_ROOT/.env"
  else
    ENV_FILE="./.env"
  fi
  unset _GIT_COMMON _REPO_ROOT
fi

if [ ! -f "$ENV_FILE" ]; then
  echo "source-env.sh: .env not found at $ENV_FILE (run from a repo/worktree, or set SFP_ENV_FILE)" >&2
  return 1 2>/dev/null || exit 1
fi

set -a
. "$ENV_FILE"
set +a

echo "source-env.sh: loaded $ENV_FILE"
