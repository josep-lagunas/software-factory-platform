#!/usr/bin/env sh
# SFP env loader.
#
# Usage (from the repo root):
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
# Point at a different file with:  SFP_ENV_FILE=/path/.env source ./source-env.sh

ENV_FILE="${SFP_ENV_FILE:-./.env}"

if [ ! -f "$ENV_FILE" ]; then
  echo "source-env.sh: .env not found at $ENV_FILE (run from repo root, or set SFP_ENV_FILE)" >&2
  return 1 2>/dev/null || exit 1
fi

set -a
. "$ENV_FILE"
set +a

echo "source-env.sh: loaded $ENV_FILE"
