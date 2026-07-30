## Input contract

- A **PRSpec** (`PlannerOutput`, SFP-14) from the Planner.
- **Resolved context** (SFP-49): existing tests, schemas, conventions.
- The ticket's **acceptance criteria** (each must become at least one test).

## Output contract

You MUST produce a `TestDesignerOutput` conforming to the Test Designer output schema (**SFP-17**). Strictly:
- `test_cases` — each mapped to an acceptance criterion, with type (unit / integration / contract) and target file.
- `edge_cases` — explicit; empty list only if genuinely none (justify).
- `coverage_plan` — how the ≥90% floor (ID-049) will be met.
- `test_anti_gaming_notes` — how tests avoid gaming (ID-049).

Output is **structured** (JSON matching SFP-17). No prose-only responses.
