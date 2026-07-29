# Plan a ready ticket

Given a parsed ticket and its resolved context, decompose the ticket into one or
more self-contained PR-sized tasks.

Emit a single JSON object with EXACTLY this top-level shape (unknown fields are
rejected):

- `pr_specs` (array, **non-empty**) — one element per PR task. Each element has
  EXACTLY these fields (unknown fields are rejected):
  - `id` (string) — stable PR-spec identifier.
  - `title` (string) — concise PR title.
  - `goal` (string) — the single outcome this PR delivers.
  - `scope` (array of short strings) — what this PR touches.
  - `out_of_scope` (array of short strings) — what is deliberately deferred.
  - `acceptance_criteria` (array of short strings) — verifiable outcomes.
  - `dependencies` (array of short strings) — upstream ids/tickets this PR needs.
  - `validation_profile` (string) — one of `"LEVEL_1_INTERNAL"`,
    `"LEVEL_2_BACKEND_OR_API"`, `"LEVEL_3_USER_FACING"`, `"LEVEL_4_HIGH_RISK"`.
  - `validation_profile_reason` (string) — why this tier was chosen.
  - `required_gates` (array of short strings) — the gates the workflow must
    enforce for this PR.
  - `likely_files_or_modules` (array of short strings) — expected file surface.
  - `risks` (array of short strings) — what could go wrong.
  - `implementation_notes` (string) — design guidance for the coder.

Rules:

- Each PR-spec must be self-contained: a single coder run can complete it
  end-to-end without another PR landing first. Use `dependencies` only for true
  ordering constraints between otherwise-independent PRs.
- Never invent product requirements. When the ticket is ambiguous, surface it as
  a `risk` and pick the higher `validation_profile` (ID-067: "when in doubt,
  choose the higher level").
- Assign every PR-spec a `validation_profile` from the four-level enum — the
  chosen tier determines which gates the workflow enforces and whether a human
  approval is required before merge.
- Produce deterministic JSON: stable field order, no prose outside the JSON
  object, no markdown fencing.
