## Input contract

You receive, from the Orchestrator (the human, during Phase A):
- A **ticket** in AI-Implementation-Specification form (ID-070): Context, Requirements, Files to create/modify, Implementation notes, References, Acceptance criteria.
- **Resolved context** (SFP-49): referenced Implementation Decisions (ID-xxx), relevant MAS sections, existing code, schemas.
- The **validation profile** assigned to the ticket (SFP-24).

## Output contract

You MUST produce a `PlannerOutput` conforming to the Planner output schema (**SFP-14 / ID-021**), as a JSON object. This output is **execution-pinned** and is structurally validated by `tools/check_prspec.py` (run after the Planner, before the Coder). The required top-level keys (a missing key fails validation; see `validate()` in that tool) are:

- `pr_spec_id`, `ticket`, `title`, `branch_name`, `validation_profile_acknowledged` — identity & profile in effect.
- `files` — non-empty list; each entry `{path: non-empty str, action: create|modify|delete}`. For `action=modify`, an `anchor` is REQUIRED and must carry EXACTLY ONE of `{before: non-empty str}` (literal text to locate) or `{line_range: [start, end]}` (ints, `start>=1`, `end>=start`, exactly 2 elements; bools rejected). An anchor that is missing, or carries both, or carries neither, fails validation. (`create`/`delete` entries may carry an anchor; it is ignored, not rejected.)
- `implementation_steps` — non-empty, ordered, deterministic, each mapped to files.
- `dependencies` — dict or list (on other tickets / ID-xxx decisions / existing code).
- `risks` — non-empty; explicitly flagged, never omitted (state "none" explicitly if so).
- `commit_plan` — `{strategy: non-empty, commit_message: non-empty}`.
- `pr_title`, `pr_body_must_include` — the PR title and the mandatory PR-body line (e.g. the JIRA link).
- `acceptance_criteria_mapping` — dict mapping each acceptance criterion to where it is satisfied (list/scalar rejected).
- `verification` — `{type: script|command, body: non-empty str}`.
- `read_allowlist` — non-empty list of paths the Coder may read.
- `rig_reference` — non-empty str naming the rig + Coder identity in effect (e.g. Phase A local execution, `sfp-coder-bot`).

Unknown/extra top-level keys are tolerated (presence + shape only); duplicate file paths are tolerated.

Output is **structured** (JSON matching SFP-14). No prose-only responses. If a path or anchor cannot be pinned (e.g. a `modify` target whose text has moved or cannot be located), emit a blocker — do not guess (MAS §12.9).
