# Design the test plan for a PR-spec

Given a parsed ticket, its resolved context, and a PR-spec, design the test plan
that proves the PR-spec meets its acceptance criteria.

Emit a single JSON object with EXACTLY this top-level shape (unknown fields are
rejected):

- `pr_spec_id` (string) — the PR-spec this plan targets.
- `test_plan` (object) — seven `list[str]` buckets. Every bucket is required
  (use an empty list when a category has no entries). Each entry is a SHORT
  description string (what to test / check), NEVER executable code bodies. The
  buckets:
  - `unit_tests` — focused unit cases.
  - `integration_tests` — multi-component cases.
  - `e2e_or_smoke_tests` — end-to-end / smoke cases.
  - `negative_tests` — failure / rejection paths.
  - `edge_cases` — boundary conditions.
  - `regression_risks` — risks this plan guards against.
  - `required_validation_commands` — shell-command strings the gates must run
    (e.g. `uv run pytest -q --cov-fail-under=90`, `uv run mypy`, `uv run ruff
    check`).

Rules (ID-066 / ID-022):
- Derive every case from the acceptance criteria; never invent behavior.
- Always include negative and edge cases; never only the happy path.
- Descriptions only — no code bodies. Commands only in
  `required_validation_commands`.
- Deterministic: the same ticket + PR-spec yields the same plan.
