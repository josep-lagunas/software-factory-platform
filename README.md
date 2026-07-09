# Software Factory Platform (SFP)

SFP is a multi-agent system that automates the spec-to-PR pipeline.

## Phase A environment setup

Copy the template and fill in real values:

    cp .env.example .env

Then load the environment into your shell:

    source ./source-env.sh

The loader auto-exports every key in `.env` (via `set -a`), so lines need no
`export` prefix. To point at a different file, set `SFP_ENV_FILE`:

    SFP_ENV_FILE=/path/to/.env source ./source-env.sh

## create-sfp-ticket skill

`tools/create_sfp_ticket.py` (stdlib) creates or updates a single SFP Jira
ticket, reusing the helpers in `tools/create_jira_tickets.py`
(`markdown_to_adf`, `build_issue_labels`, `sanitize_label`, `jira_api`,
`create_issue`). The Jira summary follows `[AREA] 🤖/👤 Title` (🤖 → `ai-agent`,
👤 → `manual`) and labels are `phase + executor + area + extras` (sanitized,
deduped). See `.claude/skills/create-sfp-ticket/SKILL.md` for the full
convention and the create / `--update` CLI.

## PRSpec linter

After the Planner emits a PRSpec (SFP-14) and before the Coder starts, the spec
is structurally validated:

    python3 tools/check_prspec.py --file <spec.json>

The linter (stdlib only) checks that every required top-level key is present,
that each `modify` entry carries exactly one anchor (`before` literal text or
`line_range`), and the other field shapes documented in the Planner output
contract (`.claude/agents/sfp-planner.md`). It exits 0 iff the spec is clean.
