# Implement one PR-spec

Given a PR-spec and its resolved context, implement that single PR-spec: create
/ modify the listed files, write/update tests, run the build and the test suite,
and open the pull request.

Emit a single JSON object with EXACTLY this top-level shape (unknown fields are
rejected). The code itself is NOT carried here — it lives on the branch/PR:

- `pr_spec_id` (string) — the PR-spec implemented.
- `branch_name` (string) — the git branch you pushed.
- `pull_request_url` (string) — the PR URL you opened.
- `files_changed` (array of strings) — paths created/modified.
- `tests_added_or_updated` (array of strings) — test paths.
- `validation_status` (string) — one of `PASSED`, `FAILED`, `PENDING`,
  `NOT_RUN`. The honest result of running the validation commands.
- `validation_evidence` (array of strings) — short evidence lines (e.g. command
  + summary; `36 passed, 100% coverage`).
- `known_limitations` (array of strings) — anything you could not honor from the
  PR-spec; empty list conveys "none reported".

Rules (ID-022 / ID-066):
- Implement the PR-spec as written; surface deviations as `known_limitations`,
  never silent improvisation.
- Reference the code (branch/PR/files), never inline it.
- Honest `validation_status` from actually running the gates.
