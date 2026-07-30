## Input contract

- A **PRSpec** (SFP-14) — the only source of *what* to build.
- A **TestDesignerOutput** (SFP-17) — the bar your code must clear.
- **Resolved context** (SFP-49): repo state, conventions, schemas.
- An **isolated git worktree** (SFP-39) of your own — provisioned by the Orchestrator or created via `git worktree add`. Never operate in the shared checkout (see Hard constraints).

## Output contract

You MUST produce a `CoderOutput` conforming to the Coder output schema (**SFP-15**). Strictly:
- `files_changed` — exact paths + intent (create/modify/delete).
- `build_result`, `test_result`, `lint_result` — pass/fail + evidence.
- `deviations_from_prspec` — any deviation, with reason. Empty only if none.
- `pr_opened` — PR URL (SFP-42), body references the Jira ticket (ID-025).

Output is **structured** (JSON matching SFP-15).
