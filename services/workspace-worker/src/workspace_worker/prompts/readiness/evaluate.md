# Evaluate readiness

Given a parsed ticket, its resolved context, and the deterministic rubric
results, evaluate the ticket's readiness to be planned against.

Emit a JSON object with EXACTLY these fields (unknown fields are rejected):

- `ticket_id` (string) — the ticket being assessed.
- `verdict` (string) — one of `"READY"`, `"NEEDS_CLARIFICATION"`,
  `"MANUAL_REQUIRED"`.
- `blocking_ambiguities` (array of short strings) — semantic gaps that block a
  `READY` verdict.
- `missing_inputs` (array of short strings) — unresolved context inputs.
- `rubric_results` (object mapping section name to boolean) — the rubric results
  you were given.

Decision rules:

- Return `"MANUAL_REQUIRED"` only when a human must make the decision (e.g.
  contradictory requirements you cannot reconcile).
- Return `"NEEDS_CLARIFICATION"` when blocking ambiguities or missing inputs
  remain.
- Return `"READY"` only when nothing blocks planning.
