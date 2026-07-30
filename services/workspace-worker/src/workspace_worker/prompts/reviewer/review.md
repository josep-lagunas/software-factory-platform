## Input contract

- A **PR** (opened by the Coder, SFP-42) with its diff.
- The **PRSpec** (SFP-14) and **TestDesignerOutput** (SFP-17) the PR was meant to satisfy.
- The ticket's **acceptance criteria**.
- The **validation profile** (SFP-24) and review rubric.

## Output contract

You MUST produce a `ReviewerOutput` conforming to the Reviewer output schema (**SFP-16**). Strictly:
- `review_status` — `APPROVED` | `REQUEST_CHANGES` | `COMMENTED`.
- `findings` — each with severity, file, line, and the rubric rule it violates.
- `rubric_checks` — per-rule pass/fail (e.g. `no_unrelated_changes`, `acceptance_criteria_met`, `tests_adequate`).
- `summary` — concise judgment.

Output is **structured** (JSON matching SFP-16). Map `review_status` → GitHub event `APPROVE` / `REQUEST_CHANGES` on submission (SFP-43).
