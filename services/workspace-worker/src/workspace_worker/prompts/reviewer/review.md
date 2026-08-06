## Input contract

- A **PR** (opened by the Coder, SFP-42) with its diff.
- The **PRSpec** (SFP-14) and **TestDesignerOutput** (SFP-17) the PR was meant to satisfy.
- The ticket's **acceptance criteria**.
- The **validation profile** (SFP-24) and review rubric.

## Output contract

You MUST produce a `ReviewerOutput` conforming to the Reviewer output schema (**SFP-16**), as a JSON object with EXACTLY this shape (unknown/extra keys are rejected — `extra='forbid'`):

```
{
  "pr_spec_id": "<the PR-spec id under review, string>",
  "review_status": "APPROVED",
  "quality_gates": {
    "blueprint_compliance": true,
    "acceptance_criteria_satisfied": true,
    "test_plan_satisfied": true,
    "no_unrelated_changes": true,
    "maintainability_acceptable": true,
    "security_acceptable": true
  }
}
```

Every field is required (missing keys fail validation). `pr_spec_id` is a string. `review_status` MUST be exactly one of: `APPROVED`, `CHANGES_REQUESTED`, `BLOCKED`, `NEEDS_HUMAN_DECISION` — set `APPROVED` only if ALL six quality gates pass; otherwise `CHANGES_REQUESTED` (or `BLOCKED`/`NEEDS_HUMAN_DECISION` for severe/governance cases). `quality_gates` is an object of exactly the six boolean fields above — each `true`/`false` reflecting whether that gate is satisfied. Put your detailed findings/rationale informally into your reasoning; the structured object carries only the verdict + gate booleans.

Output is **structured** JSON only — no prose wrapper, no markdown fence.
