---
name: create-sfp-ticket
description: Create or update a single SFP Jira ticket (Task) with the canonical summary/label conventions — `[AREA] 🤖/👤 Title` summary, labels = phase + executor + area + extras. Wraps tools/create_sfp_ticket.py, which reuses tools/create_jira_tickets.py helpers (markdown_to_adf, build_issue_labels, sanitize_label, jira_api, create_issue). Use when creating or normalizing one ticket, not the full hierarchy batch.
---

# create-sfp-ticket

Create or update **one** SFP Jira ticket (a Task in the SFP project) following
the canonical conventions. The batch creator (`tools/create_jira_tickets.py`)
reads the whole hierarchy; this skill is for the single-ticket case — adding one
ticket or normalizing an existing one. See ID-070 (ticket template) and
`tools/create_jira_tickets.py` for the reused helpers.

## Scope — Phase A manual bootstrap ONLY

> This skill documents how *I* (Claude Code, playing the Coder during the manual
> bootstrap) create/normalize a single ticket. The **policy** (summary shape,
> label rule, emoji↔executor map) persists into Phase B; the **mechanism** (this
> stdlib CLI hitting the Jira REST API directly) is a Phase A stand-in. Phase B's
> Orchestrator owns ticket creation/transition programmatically (ID-072).

## When to use

- You need to create exactly one SFP Task ticket (not rerun the whole batch).
- You need to normalize an existing ticket's summary / labels / description to
  the canonical format (e.g. after a format convention change).

## Conventions (non-negotiable)

### Heading + summary

- Ticket heading (in docs/specs): `### SFP-N [AREA] 🤖/👤 — Title`
- Jira summary (built by the CLI): `[AREA] 🤖/👤 Title` — constructed from
  `--area`, `--executor`, `--title`.

### Emoji ↔ executor ↔ label map

| emoji              | executor (CLI) | Jira label  |
|--------------------|----------------|-------------|
| 🤖 (`U+1F916`)     | `bot`          | `ai-agent`  |
| 👤 (`U+1F464`)     | `human`        | `manual`    |

Inverse (used when normalizing an existing ticket): `ai-agent` → 🤖,
`manual` → 👤.

### Labels

`labels = [phase, executor_label, area, *extras]` where:

- **phase** ∈ {`manual-core`, `platform`} (`--phase`),
- **executor_label** ∈ {`ai-agent`, `manual`} (derived from `--executor`),
- **area** (from `--area`),
- **extras** (`--labels a,b,c`) — sanitized (spaces → `-`) and deduped,
  order-preserving.

Sanitization + dedupe are delegated to `cjt.build_issue_labels` /
`cjt.sanitize_label` — **never reimplemented** in the single-ticket helper.

### Body (ID-070 AI Implementation Specification)

The description body follows the ID-070 template: **Context; Requirements;
Files to create/modify; Implementation notes; References; Context outputs /
required inputs; Acceptance criteria** (checklist). A Manual ticket additionally
has "Human action required" rationale, "What the human must do" steps, and a
Verification checklist. The CLI converts the markdown body to ADF via
`cjt.markdown_to_adf`.

## CLI

Create (`--description` is a path to the markdown body, or `-` for stdin):

```bash
python3 tools/create_sfp_ticket.py \
  --title "Title" --area SPEC --executor bot --phase platform \
  --labels "extra-one,extra two" --description path/to/body.md
# -> prints the new issue key, e.g. SFP-194
```

Update. `--description-source existing` reuses the ticket's current description
(an ADF dict passes through verbatim — R1 — a plain string is re-converted via
`markdown_to_adf`); `file` reads `--description-file`. `--labels-mode merge` =
existing ∪ parsed (deduped, existing order first); `replace` = drop existing,
keep only sanitized parsed labels.

```bash
python3 tools/create_sfp_ticket.py --update \
  --key SFP-XXX --summary "[SPEC] 🤖 Title" \
  --description-source existing --labels-mode merge --labels "new-label"
```

Environment (required before any network call): `JIRA_SITE`, `JIRA_EMAIL`,
`JIRA_API_TOKEN` (optionally `JIRA_PROJECT`, default `SFP`).

## References

ID-070 (ticket template), ID-065 (AI/manual executor), `tools/create_jira_tickets.py`
(reused helpers: `markdown_to_adf`, `build_issue_labels`, `sanitize_label`,
`jira_api`, `create_issue`).
