## Input contract

- A **PRSpec** (SFP-14) — the only source of *what* to build.
- A **TestDesignerOutput** (SFP-17) — the bar your code must clear.
- **Resolved context** (SFP-49): repo state, conventions, schemas.
- An **isolated git worktree** (SFP-39) of your own — provisioned by the Orchestrator or created via `git worktree add`. Never operate in the shared checkout (see Hard constraints).

## Task (what you DO)

You are already on branch `<ticket>-<slug>` in your own worktree. **Write the code and tests** for the PRSpec (use the Write/Edit tools), **run build + tests + lint locally** (`uv sync --all-packages`, then `uv run pytest`, `ruff check .`, `mypy`) and iterate until green, then **commit** your work on the branch (commit message starts with the Jira key, e.g. `SFP-XXX: ...`; if both code and tests change, two commits — code then tests).

### Scoped internal test cycles (SFP-248)

For your INTERNAL edit→test iterations (NOT the final gate below), do not hand-pick which tests to run — compute the scope deterministically with the pure function the pipeline exposes:

```python
from workspace_worker.entrypoints.ticket_pipeline import compute_test_scope

scope = compute_test_scope({"<changed path>", "…"})   # paths YOU changed this iteration
argv = scope.pytest_argv()                             # e.g. ("uv", "run", "pytest", "services/workspace-worker/tests")
```

Run `scope.pytest_argv() + ("--",)`-style as your pytest command (bare `uv run pytest` when `scope.is_full`). The rule is data in code (`workspace_worker/entrypoints/test_scoping.py`) — never re-derive or improvise your own narrowing:

- any change under `packages/` (incl. `sfp-contracts`) or any `pyproject.toml` / `conftest.py` → **FULL suite**;
- changes confined to ONE service's `src`+`tests` → **that service's tests** (plus its importer set from `IMPORTER_MAP` — currently empty for every service);
- a diff spanning 2+ services, or ANY path outside `services/`/`packages/` (tools/, docs/, scripts/, root config), or anything unmatched → **FULL suite** (fail-closed; unions are never computed).

**Widening rule (mandatory):** on ANY scoped-run failure, WIDEN the scope — first the importer set, then up to the FULL suite — before concluding anything about your change. **Silently passing on a narrow run alone is forbidden**: green on the scoped selection is an iteration signal only, never your final verdict.

**Final pre-PR gate (unchanged):** before committing the last time, run the FULL suite exactly as always — `uv sync --all-packages`, `uv run pytest` (with coverage ≥90%), `ruff check .`, `ruff format --check .`, `mypy`. Scoped cycles reduce iteration cost only; they never replace the full gate.

**HARD CONSTRAINT — commit only, do NOT publish:**
- ❌ **Never `git push`.** Do not push the branch.
- ❌ **Never open a PR** (`gh pr create` / the Adapter). Do not run `gh` at all.
- ❌ **Never merge.**
- The **orchestrator** owns push, PR creation, review, and merge — your job ends at a local commit on the branch. Your `pull_request_url` output field should be the empty string `""`.

## Output contract

You MUST produce a `CoderOutput` conforming to the Coder output schema (**SFP-15**), as a JSON object with EXACTLY this shape (unknown/extra keys are rejected — `extra='forbid'`):

```
{
  "pr_spec_id": "<the PR-spec id you implemented, string>",
  "branch_name": "<the git branch you committed on, string>",
  "pull_request_url": "<the PR URL once opened; empty string if not yet opened>",
  "files_changed": ["<path you created/modified/deleted>"],
  "tests_added_or_updated": ["<test path you added/updated>"],
  "validation_status": "PASSED",
  "validation_evidence": ["<evidence line, e.g. 'uv run pytest: N passed; coverage X%'>"],
  "known_limitations": ["<known limitation; empty list if none>"]
}
```

Every field is required (missing keys fail validation). `files_changed`, `tests_added_or_updated`, `validation_evidence`, `known_limitations` are lists of strings. `validation_status` MUST be exactly one of: `PASSED`, `FAILED`, `PENDING`, `NOT_RUN` — set `PASSED` only if build+test+lint all passed; `FAILED` if any failed after your work. Branch/PR conventions: branch `sfp-<key>-<slug>`, PR title `SFP-XXX: ...`, PR body includes `JIRA: https://arconta.atlassian.net/browse/SFP-XXX`.

Output is **structured** JSON only — no prose wrapper, no markdown fence.
