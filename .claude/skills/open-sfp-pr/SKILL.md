---
name: open-sfp-pr
description: Open a bot-authored SFP pull request. Enforces the SFP PR conventions — title prefixed with the Jira key (SFP-XXX: ...), a `JIRA: <url>` line in the body, created as `sfp-coder-bot` via `gh`+`GH_TOKEN`, branch `sfp-<key>-<slug>` → main. Use whenever the Coder opens a PR for an SFP ticket.
---

# open-sfp-pr

Open a pull request for an SFP ticket **as `sfp-coder-bot`**, following the SFP conventions. This skill captures the exact approach already in use across SFP PRs.

## Scope — Phase A manual bootstrap ONLY

> ⚠️ This skill is a **Phase A artifact** — it documents how *I* (Claude Code, playing the Coder during the manual bootstrap) open PRs. **Phase B's Workspace Worker does NOT use this skill, `gh`, SSH, or `gh pr merge --squash`.** Phase B opens/merges PRs programmatically through the **Git Provider Adapter** (ID-035, SFP-41/42/43/44 — httpx → GitHub API, token injected per-task). See memory `sfp-phase-a-vs-phase-b-git`.
>
> What **persists** into Phase B is the **policy** (below); what **does not** is the **mechanism** (`gh`, `GH_TOKEN`, SSH host alias, `gh pr merge`).
>
> **Policy (conventions) — apply to both phases** (Phase B's Adapter enforces them):
> - PR title starts with the Jira key: `SFP-XXX: <title>`.
> - PR body has a `JIRA: [SFP-XXX](…)` line.
> - Commit messages start with the Jira key.
>
> **Mechanism — Phase A only** (what this skill's commands use):
> - `gh` CLI with `GH_TOKEN=$GITHUB_TOKEN_CODER`, SSH host alias push, `gh pr merge --squash`. Phase B replaces all of this with the Adapter.

## When to use

The Coder has finished implementation on a branch, run build/tests locally, and is ready to open the PR for review. (Commits and push already happened as `sfp-coder-bot` via the repo's local git config + SSH host alias.)

## Inputs

- `KEY` — the Jira ticket key, e.g. `SFP-23`.
- `TITLE` — a short, human-readable title for the work (the Jira key is added by the convention; don't include it in TITLE).
- `BODY` — the PR description content (see template below).

## Conventions (non-negotiable)

1. **Title** = `KEY: TITLE` → e.g. `SFP-23: Monorepo skeleton: services/* + packages/*`.
2. **Body** must contain a `JIRA:` line whose visible text is **just the Jira ID**, hyperlinked to the task:
   `JIRA: [SFP-XXX](https://arconta.atlassian.net/browse/SFP-XXX)` → renders as `JIRA: SFP-189`.
3. **Author = `sfp-coder-bot`** — create via `gh` with `GH_TOKEN=$GITHUB_TOKEN_CODER`. Never plain `gh` (that authenticates as the human and mis-attributes the PR).
4. **Base:** `main`.
5. The branch was already created as `sfp-<key-lowercased>-<slug>` when the Coder started work (e.g. `sfp-23-monorepo-skeleton`).

## Commits (when the PR includes code AND tests)

- If the PR contains both code and tests: **two commits**, in order — **1st the code, 2nd the tests.**
- If the PR is code-only, tests-only, or doc-only: a single commit.
- **Every commit message starts with the Jira key** — `SFP-XXX: <subject>`.
- On merge, all commits are **squashed into one** (`gh pr merge --squash`); the squash commit title is `SFP-XXX: <title> (#N)`. So the history stays one-commit-per-PR on `main`, while the branch cleanly separates code from tests for review.

## PR body template (match this structure)

```markdown
## Summary
<1–3 sentences: what this PR does and why.>

JIRA: [SFP-XXX](https://arconta.atlassian.net/browse/SFP-XXX)

## Changes
- <bullet list of meaningful changes>

## Acceptance criteria
- [ ] <criterion from the ticket>
- [ ] <criterion>

## Provenance
Fully bot-authored: committed, pushed, and PR-opened by `sfp-coder-bot`.

## References
<ticket refs: ID-xxx, MAS §, SFP-xxx>
```

(For a doc-only / LEVEL_1 PR, "Changes" can be a short paragraph; keep the JIRA line and the structure.)

## Command

```bash
source .env   # loads GITHUB_TOKEN_CODER (gitignored .env)
GH_TOKEN="$GITHUB_TOKEN_CODER" gh pr create \
  --title "SFP-XXX: <TITLE>" \
  --base main \
  --body "$(cat <<'EOF'
## Summary
...

JIRA: [SFP-XXX](https://arconta.atlassian.net/browse/SFP-XXX)

## Changes
- ...

## Acceptance criteria
- [ ] ...

## Provenance
Fully bot-authored: committed, pushed, and PR-opened by `sfp-coder-bot`.

## References
...
EOF
)"
```

## Verify after opening

```bash
source .env
GH_TOKEN="$GITHUB_TOKEN_CODER" gh pr view --json number,author,url \
  --jq '"PR #\(.number) | author: \(.author.login) | \(.url)"'
```

`author` MUST be `sfp-coder-bot`. If it's `josep-lagunas`, the `GH_TOKEN` override didn't apply — stop and redo.

## After the PR is open — pipeline (per the corrected flow)

This skill only opens the PR. The rest of the pipeline is owned by other roles:
1. **Reviewer** (`sfp-reviewer-bot`, via `gh pr review` + `GH_TOKEN=$GITHUB_TOKEN_REVIEWER`).
2. **Validation Profile** (SFP-57, post-review): assign LEVEL_1–4. LEVEL_1 (no-impact) → auto-merge; LEVEL_2+ → human approval required (ID-024/ID-067).
3. **Merge** (`sfp-coder-bot`, via `gh pr merge --squash --delete-branch` + `GH_TOKEN=$GITHUB_TOKEN_CODER`) — only on Orchestrator authorization (the human in Phase A) and only after the Reviewer + validation profile pass.
4. **Move the Jira ticket to Done** — once the PR is merged, transition `SFP-XXX` → `Done` in Jira. This is an **Orchestrator** workflow-control side effect (ID-072: Jira status transitions are centralized in the Orchestrator, *not* the Coder); in Phase A it is performed on the Orchestrator's behalf (the Jira API call uses `$JIRA_EMAIL`/`$JIRA_API_TOKEN` from `.env`, transition id `51` → Done). Never skip this — a merged PR without a Done ticket desynchronizes Jira from GitHub.

## References

ID-025 (PR references the Jira ticket), ID-073 (role-scoped Git identity), MAS §9.6. See `.claude/agents/sfp-coder.md` for the full Coder operational contract.
