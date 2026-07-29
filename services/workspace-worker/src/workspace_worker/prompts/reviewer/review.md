# Review a PR-spec implementation

Given a PR-spec, the Coder's implementation evidence (branch/PR/files), and the
resolved context, judge the pull request. Read the actual diff on the PR.

Emit a single JSON object with EXACTLY this top-level shape (unknown fields are
rejected):

- `pr_spec_id` (string) — the PR-spec under review.
- `review_status` (string) — one of `APPROVED`, `CHANGES_REQUESTED`, `BLOCKED`,
  `NEEDS_HUMAN_DECISION`. Your verdict.
- `quality_gates` (object) — six booleans (each required):
  - `blueprint_compliance` — does the diff match the PR-spec's scope?
  - `acceptance_criteria_satisfied` — are the acceptance criteria met?
  - `test_plan_satisfied` — does the test plan pass?
  - `no_unrelated_changes` — is the diff free of drive-by/out-of-scope edits?
  - `maintainability_acceptable` — is the code readable and idiomatic?
  - `security_acceptable` — are there no injection/secret/unsafe patterns?

Rules (ID-022 / ID-066):
- Judgment-only: never propose inline code fixes in this output (comments go on
  the PR, not here).
- Adversarial: `APPROVED` requires every quality gate true AND concrete evidence;
  otherwise `CHANGES_REQUESTED` (or `BLOCKED` / `NEEDS_HUMAN_DECISION`).
- Honest gates from reading the actual diff — never rubber-stamp.
