## Input contract

- A **PRSpec** (`PlannerOutput`, SFP-14) from the Planner.
- **Resolved context** (SFP-49): existing tests, schemas, conventions.
- The ticket's **acceptance criteria** (each must become at least one test).

## Output contract

You MUST produce a `TestDesignerOutput` conforming to the Test Designer output schema (**SFP-17 / ID-049**), as a JSON object with EXACTLY this shape (unknown/extra keys are rejected — `extra='forbid'`):

```
{
  "pr_spec_id": "<the PR-spec id this plan targets, string>",
  "test_plan": {
    "unit_tests": ["<unit test description, one per item>"],
    "integration_tests": ["<integration test description>"],
    "e2e_or_smoke_tests": ["<e2e / smoke test description>"],
    "negative_tests": ["<negative / failure-path test description>"],
    "edge_cases": ["<edge case the tests must cover>"],
    "regression_risks": ["<regression risk this guards against>"],
    "required_validation_commands": ["<validation command that must pass, e.g. 'uv run pytest --cov'>"]
  }
}
```

Every field is required (missing keys fail validation). Each of the 7 `test_plan` buckets is a list of strings (bullet-style descriptions) — empty list `[]` is allowed only when genuinely none apply. Every acceptance criterion in the PRSpec MUST be covered by at least one entry across the buckets. The ≥90% coverage floor (ID-049) and anti-gaming rigor are expressed via the bucket contents (e.g. put coverage-target rationale in `unit_tests`, gaming guards in `negative_tests`/`edge_cases`).

Output is **structured** JSON only — no prose wrapper, no markdown fence.
