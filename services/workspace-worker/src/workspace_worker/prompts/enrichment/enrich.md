# Enrich a blocked ticket

Rewrite the blocked ticket's description so the readiness gate can pass it.

You receive, in your context:

- `ticket_id` — the ticket you are enriching.
- `ticket` — the ticket's current eight ID-070 sections (the starting text).
- `gate` — the readiness gate's structured blocking output, verbatim:
  `blocking_ambiguities`, `missing_inputs`, and `rubric_failed`. Every entry
  here must be explicitly addressed in your enriched description.
- `template` — per-ticket-type template inputs (e.g. emitter / transition /
  hosting clone shapes). Follow its structure where it applies; do not
  contradict it.
- `grounding_symbols` — the read-verified symbols that exist on landed main.
  These are the ONLY symbols you may cite in backticks.
- `prior_iteration_failures` (retry iterations only) — the exact reasons your
  previous candidate was rejected. Fix every line; do not re-emit the same
  defect.

Emit a JSON object with EXACTLY this field (unknown fields are rejected):

```json
{"description": "<the enriched description as Markdown>"}
```

The `description` value is Markdown whose body is the full enriched ticket:
the eight literal section headers (`# Context`, `# Requirements`,
`# Files to create/modify`, `# Implementation notes`, `# References`,
`# Context outputs / required inputs`, `# Acceptance criteria`,
`# Dependencies`), each followed by a non-empty body. Nothing outside the
eight sections — no preamble, no closing remark.

If — and only if — the grounded surface genuinely cannot support resolving a
blocking item, resolve it fail-closed: state in the relevant section what is
unverifiable and what a human must confirm, rather than inventing content.
