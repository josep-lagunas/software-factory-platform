# SFP — Ticket Hierarchy (Blueprint Skeleton)

Project: Software Factory Platform (SFP) — building SFP itself.
Source of truth: `MASTER_SPECIFICATION_V0.md` (v0.1.3) + `IMPLEMENTATION_DECISIONS.md` (v1.0).
Template: ID-070 (deterministic AI Implementation Specification). AI/Manual: ID-065. Context I/O: ID-071.
Granularity rule: one ticket ≈ one PR-sized, reviewable unit (ARCONTA precedent; MAS §12.9).
Conventions: `🤖` = AI-executable, `👤` = Manual prerequisite. Each ticket carries a **Labels** line with three Jira labels: phase (`manual-core` = self-contained/manually runnable; `platform` = the deployed platform), executor (`ai-agent` / `manual`), and area (epic).
Dependency invariant: **no `manual-core` ticket depends on a `platform` ticket.** `platform` → `manual-core` is allowed.
Jira project key: `SFP` (confirmable).

This is the **skeleton**: ticket titles, executor, phase, dependencies, and key context inputs/outputs. Full deterministic bodies (Context, Requirements, Files to create/modify, Implementation notes, References, Acceptance criteria) are filled in a subsequent pass.

**17 Epics · 170 Tickets** (SFP-1 to SFP-170)

| Epic | Phase | Range | Tickets |
|---|---|---|---|
| PREREQ | Manual Core | SFP-1 → SFP-4 | 4 |
| WORKSPACE | Manual Core | SFP-5 → SFP-12 | 8 |
| CONTRACTS | Manual Core | SFP-13 → SFP-24 | 12 |
| SHARED-FRAMEWORK | Manual Core | SFP-25 → SFP-35 | 11 |
| AGENT-LAYER | Manual Core | SFP-36 → SFP-58 | 23 |
| LOCAL-DEV-RUNBOOK | Manual Core | SFP-59 → SFP-63 | 5 |
| PREREQ | Platform | SFP-64 → SFP-69 | 6 |
| PLATFORM-INFRA | Platform | SFP-70 → SFP-81 | 12 |
| PERSISTENCE | Platform | SFP-82 → SFP-96 | 15 |
| MESSAGING-INFRA | Platform | SFP-97 → SFP-102 | 6 |
| EXTERNAL-EVENTS | Platform | SFP-103 → SFP-107 | 5 |
| IDENTITY | Platform | SFP-108 → SFP-111 | 4 |
| COMMUNICATION | Platform | SFP-112 → SFP-119 | 8 |
| ORCHESTRATOR | Platform | SFP-120 → SFP-142 | 23 |
| WORKSPACE-WORKER | Platform | SFP-143 → SFP-154 | 12 |
| CI-CD | Platform | SFP-155 → SFP-162 | 8 |
| VALIDATION | Platform | SFP-163 → SFP-170 | 8 |

---

# MANUAL CORE — self-contained, manually runnable (label: `manual-core`)

## PREREQ Epic — Manual prerequisites · Manual Core 👤

### SFP-1 [PREREQ] 👤 — Create SFP GitHub monorepo
**Labels:** manual-core, manual, prereq | **Deps:** — | **Context out:** `repo_url`, `default_branch`

**Context:**
The SFP codebase lives in a single GitHub monorepo (ID-002). This empty repo is the root every code ticket is created in; nothing else can run without it.

**Human action required:**
Repository creation requires a GitHub account and ownership/visibility decisions; it cannot be executed by an agent.

**What the human must do:**
1. Create a new GitHub repository (e.g. `sfp`), empty — no README/.gitignore (SFP-5 initializes it).
2. Set the default branch to `main`.
3. Grant CI/automation push access for future releases.
4. Record the clone URL.

**Verification:**
- [ ] Repo exists and is empty
- [ ] Default branch is `main`
- [ ] Clone URL recorded

**References:** ID-002, ID-045, ID-056.

---

### SFP-2 [PREREQ] 👤 — Obtain LLM provider API key (Anthropic-compatible)
**Labels:** manual-core, manual, prereq | **Deps:** — | **Context out:** `llm_provider_secret_ref`, `anthropic_base_url`

**Context:**
The Agent Runtime (SFP-36) drives the Planner/Coder/Reviewer via the Claude Agent SDK against an Anthropic-compatible endpoint (ID-018/019). A key and base URL are required and must be held as a secret reference, never committed.

**Human action required:**
Account creation, billing, key generation, and key custody are human tasks.

**What the human must do:**
1. Choose an Anthropic-compatible provider for v0 (Anthropic direct, or Z.ai GLM pending the ID-018/019 integration gate).
2. Create an API key.
3. Note the base URL (e.g. `https://api.anthropic.com` or `https://api.z.ai/api/anthropic`).
4. Store the key as a secret reference via the local secret provider (SFP-12); Secrets Manager in prod (ID-016). Never write the value into the repo.

**Verification:**
- [ ] API key stored as a secret reference (not in plaintext)
- [ ] Base URL recorded
- [ ] A test call from the Agent Runtime succeeds (verified at SFP-36)

**References:** ID-016, ID-018, ID-019, ID-020, ID-063.

---

### SFP-3 [PREREQ] 👤 — Create SFP Jira project + API token
**Labels:** manual-core, manual, prereq | **Deps:** — | **Context out:** `jira_project_key` (`SFP`), `jira_api_token_secret_ref`

**Context:**
SFP tracks its own backlog in Jira under project `SFP` (mirroring how ARCONTA used `PRD`). An API token is needed to upload and manage tickets.

**Human action required:**
Project and token creation require a Jira admin account.

**What the human must do:**
1. Create a Jira project with key `SFP` (company-managed/software).
2. Create an API token for a service account.
3. Store token + site URL + account email as secret references.

**Verification:**
- [ ] Project `SFP` exists
- [ ] API token stored as secret reference
- [ ] A read call (GET project) succeeds

**References:** ID-045, ID-072 (Jira as source of truth for work lifecycle).

---

### SFP-4 [PREREQ] 👤 — Configure local development environment
**Labels:** manual-core, manual, prereq | **Deps:** — | **Context out:** `dev_env_ready`

**Context:**
All Phase A code runs locally during the manual bootstrap. Python 3.13 (ID-047), `uv` (ID-048), and Docker (for compose/sandbox) must be installed and verified.

**Human action required:**
Tool installation on the developer machine.

**What the human must do:**
1. Install Python 3.13; verify `python3.13 --version`.
2. Install `uv`; verify `uv --version`.
3. Install Docker + Compose; verify `docker compose version`.
4. Confirm Git is configured.

**Verification:**
- [ ] `python3.13 --version` reports 3.13.x
- [ ] `uv --version` succeeds
- [ ] `docker compose version` succeeds
- [ ] `git --version` succeeds

**References:** ID-047, ID-048, ID-055.

---

## WORKSPACE Epic 🤖

### SFP-5 [WORKSPACE] 🤖 — uv workspace + root pyproject + single uv.lock
**Labels:** manual-core, ai-agent, workspace | **Deps:** SFP-1, SFP-4 | **Context in:** `repo_url`, `dev_env_ready`

**Context:**
The monorepo is a single `uv` workspace with one root lockfile (ID-048). This is the foundation every package and service builds on.

**Requirements:**
- Root `pyproject.toml` declaring the workspace and members (`services/*`, `packages/*`).
- `requires-python = ">=3.13,<3.14"` (ID-047).
- Pin `uv`; generate and commit a single root `uv.lock`.
- `.python-version`, `.gitignore`, `.editorconfig`.

**Files to create/modify:**
- `pyproject.toml` — workspace root + tool-config anchors
- `uv.lock` — generated, committed
- `.python-version` — `3.13`
- `.gitignore`, `.editorconfig`

**Implementation notes:**
- Declare members via `[tool.uv.workspace]`.
- Shared dev dependencies (ruff, mypy, pytest) at workspace root.

**References:** ID-002, ID-047, ID-048.

**Acceptance criteria:**
- [ ] `uv sync` succeeds with no members yet
- [ ] `uv run python --version` reports 3.13.x
- [ ] Single root `uv.lock` committed
- [ ] ≥90% coverage on any added code

---

### SFP-6 [WORKSPACE] 🤖 — Monorepo skeleton: services/*, packages/*
**Labels:** manual-core, ai-agent, workspace | **Deps:** SFP-5

**Context:**
Create the directory structure for the five services and the shared packages (ID-002, Implementation Notes §4).

**Requirements:**
- `services/{external-events,identity,communication,orchestrator,workspace-worker}/` each a stub Python project (`pyproject.toml`, `src/`, `tests/`).
- `packages/{sfp-contracts,sfp-messaging,sfp-observability,sfp-testing,sfp-agent-runtime,sfp-config}/` stubs.
- Register all as workspace members.

**Files to create/modify:**
- Per service/package: `pyproject.toml`, `src/<pkg>/__init__.py`, `tests/__init__.py`

**Implementation notes:**
- Shared packages are consumed as workspace path dependencies.

**References:** ID-002, ID-048, Implementation Notes §4.

**Acceptance criteria:**
- [ ] `uv sync` resolves all members
- [ ] Each package importable
- [ ] `uv run pytest` exits 0 (no tests)

---

### SFP-7 [WORKSPACE] 🤖 — Uniform per-service layout template
**Labels:** manual-core, ai-agent, workspace | **Deps:** SFP-6

**Context:**
Every service uses the same internal layout (Implementation Notes §3) to reduce cognitive load and enable tooling.

**Requirements:**
- Per service: `src/<svc>/{domain,application,interfaces,infrastructure,entrypoints}/__init__.py`.
- Document the dependency-direction rule (entrypoints → domain) in each service README.

**Files to create/modify:**
- The five layer directories per service
- `services/<svc>/README.md` (layout + dependency rule)

**Implementation notes:**
- Dependencies flow inward: entrypoints → domain; domain has no infra imports.
- Mirror the layout across all services for tooling uniformity (Implementation Notes §3).

**References:** Implementation Notes §3.

**Acceptance criteria:**
- [ ] All five layers present in each service
- [ ] README documents the dependency-direction rule
- [ ] Lint/test pass

---

### SFP-8 [WORKSPACE] 🤖 — ruff configuration (lint + format)
**Labels:** manual-core, ai-agent, workspace | **Deps:** SFP-5

**Context:**
Uniform lint and format across the workspace (ID-062).

**Requirements:**
- Root `[tool.ruff]` (line-length, target `py313`, rule selection) and `[tool.ruff.format]`.

**Files to create/modify:**
- `pyproject.toml` — `[tool.ruff]`, `[tool.ruff.format]`

**Implementation notes:**
- Configure at workspace root so it applies to all packages/services.
- Use ruff format (replaces black); select a sensible rule set (e.g. E,F,I,UP,B).

**References:** ID-062, ID-047.

**Acceptance criteria:**
- [ ] `uv run ruff check .` passes (0 errors on skeleton)
- [ ] `uv run ruff format --check .` passes

---

### SFP-9 [WORKSPACE] 🤖 — mypy configuration
**Labels:** manual-core, ai-agent, workspace | **Deps:** SFP-5

**Context:**
Static typing (ID-062).

**Requirements:**
- Root `[tool.mypy]` with `python_version = 3.13` and a strict baseline.

**Files to create/modify:**
- `pyproject.toml` — `[tool.mypy]`

**Implementation notes:**
- Start strict on packages/sfp-contracts; relax per-service as needed.
- Run from the workspace root.

**References:** ID-062.

**Acceptance criteria:**
- [ ] `uv run mypy packages services` succeeds

---

### SFP-10 [WORKSPACE] 🤖 — pytest + pytest-asyncio + coverage (90% gate)
**Labels:** manual-core, ai-agent, workspace | **Deps:** SFP-5

**Context:**
The test framework with an enforced coverage floor (ID-049).

**Requirements:**
- pytest, pytest-asyncio (`asyncio_mode = auto`), and coverage config.
- `--cov-fail-under=90` enforced in CI.

**Files to create/modify:**
- `pyproject.toml` — `[tool.pytest.ini_options]`, `[tool.coverage.*]`

**Implementation notes:**
- asyncio_mode=auto so async tests need no marker.
- Coverage measured per project; fail-under=90 enforced in CI (ID-049).

**References:** ID-049.

**Acceptance criteria:**
- [ ] `uv run pytest --cov` runs
- [ ] Coverage fail-under=90 configured

---

### SFP-11 [WORKSPACE] 🤖 — sfp-config: typed config + env loading
**Labels:** manual-core, ai-agent, workspace | **Deps:** SFP-6

**Context:**
Configuration is typed and loaded from the environment, never hardcoded (ID-052, MAS §10.7).

**Requirements:**
- `sfp-config` package: base `Settings` (pydantic-settings), env loading, typed config objects.
- A `SecretRef` type representing an opaque secret reference (resolved by a provider).

**Files to create/modify:**
- `packages/sfp-config/src/sfp_config/{__init__.py,settings.py,secrets.py}`
- `packages/sfp-config/tests/`

**Implementation notes:**
- Use `pydantic-settings`; secrets are held as references, never values.

**References:** ID-016, ID-052, MAS §10.7.

**Acceptance criteria:**
- [ ] Settings load from env
- [ ] `SecretRef` type exists (no value held)
- [ ] ≥90% coverage

---

### SFP-12 [WORKSPACE] 🤖 — sfp-config: local secret provider (env/file)
**Labels:** manual-core, ai-agent, workspace | **Deps:** SFP-11

**Context:**
Local dev resolves secret references from env or a local file (ID-054/016), so local development never needs real Secrets Manager.

**Requirements:**
- `LocalSecretProvider` implementing the secret-resolution interface (reads env vars and a gitignored local file).
- Wired via the composition root in dev.

**Files to create/modify:**
- `packages/sfp-config/src/sfp_config/providers/local.py`
- `packages/sfp-config/tests/`

**Implementation notes:**
- Resolve SecretRef from env first, then a gitignored local file.
- Never log resolved values. Prod provider (Secrets Manager) is SFP-78.

**References:** ID-016, ID-054.

**Acceptance criteria:**
- [ ] Provider resolves a `SecretRef` to a value from env/file
- [ ] Local secrets file is gitignored
- [ ] ≥90% coverage

---

## CONTRACTS Epic — sfp-contracts 🤖

### SFP-13 [CONTRACTS] 🤖 — Common agent output envelope schema
**Labels:** manual-core, ai-agent, contracts | **Deps:** SFP-6

**Context:**
Every agent returns JSON validating against a common envelope; the Orchestrator decides only from structured fields (ID-066).

**Requirements:**
- Pydantic model `AgentOutput{schema_version, agent, ticket_id, timestamp, status, payload, human_readable_summary}`.
- `status ∈ {SUCCESS, FAILED, BLOCKED, NEEDS_HUMAN, NEEDS_RETRY}`.
- JSON serialization/deserialization helpers.

**Files to create/modify:**
- `packages/sfp-contracts/src/sfp_contracts/agents/{envelope.py,status.py}`
- `packages/sfp-contracts/tests/agents/`

**Implementation notes:**
- Pydantic v2 models; reject unknown fields (extra='forbid').
- The status enum drives Orchestrator branching (ID-066).

**References:** ID-013, ID-066.

**Acceptance criteria:**
- [ ] Envelope validates well-formed payloads and rejects malformed ones
- [ ] All `status` values covered by tests
- [ ] ≥90% coverage

---

### SFP-14 [CONTRACTS] 🤖 — Planner output schema (`pr_specs`)
**Labels:** manual-core, ai-agent, contracts | **Deps:** SFP-13

**Context:**
The Planner decomposes a ticket into PR-specs; its output must be deterministic and machine-consumable (ID-021, ID-066).

**Requirements:**
- `PlannerOutput` payload: `pr_specs[]` with `{id, title, goal, scope, out_of_scope, acceptance_criteria, dependencies, validation_profile, validation_profile_reason, required_gates, likely_files_or_modules, risks, implementation_notes}`.

**Files to create/modify:**
- `packages/sfp-contracts/src/sfp_contracts/agents/planner.py`

**Implementation notes:**
- pr_specs.min_items=1; validation_profile is the SFP-24 enum.
- Reuse context-types from SFP-19 for any typed fields.

**References:** ID-021, ID-066, ID-067.

**Acceptance criteria:**
- [ ] Validates a fully-populated `pr_spec`
- [ ] Rejects payloads missing required fields
- [ ] ≥90% coverage

---

### SFP-15 [CONTRACTS] 🤖 — Coder output schema
**Labels:** manual-core, ai-agent, contracts | **Deps:** SFP-13

**Context:**
The Coder reports implementation evidence by reference (the code lives on the branch), never the code body (ID-066).

**Requirements:**
- `CoderOutput` payload: `{pr_spec_id, branch_name, pull_request_url, files_changed, tests_added_or_updated, validation_status, validation_evidence, known_limitations}`, `validation_status ∈ {PASSED, FAILED, PENDING, NOT_RUN}`. No code body field.

**Files to create/modify:**
- `packages/sfp-contracts/src/sfp_contracts/agents/coder.py`

**Implementation notes:**
- Never include file contents — code lives on the branch.
- validation_status drives Orchestrator rework decisions.

**References:** ID-066, ID-022.

**Acceptance criteria:**
- [ ] No code-body field exists
- [ ] Validates conformant payloads
- [ ] ≥90% coverage

---

### SFP-16 [CONTRACTS] 🤖 — Reviewer output schema (judgment-only)
**Labels:** manual-core, ai-agent, contracts | **Deps:** SFP-13

**Context:**
The Reviewer returns holistic PR-level judgments only; review comments live on GitHub and deterministic facts (CI/gate status) are not echoed (ID-066).

**Requirements:**
- `ReviewerOutput`: `{pr_spec_id, review_status, quality_gates}`.
- `review_status ∈ {APPROVED, CHANGES_REQUESTED, BLOCKED, NEEDS_HUMAN_DECISION}`.
- `quality_gates ∈ {blueprint_compliance, acceptance_criteria_satisfied, test_plan_satisfied, no_unrelated_changes, maintainability_acceptable, security_acceptable}` (booleans).
- **No** `comments[]`; **no** `ci_passed` / `validation_profile_gates_satisfied`.

**Files to create/modify:**
- `packages/sfp-contracts/src/sfp_contracts/agents/reviewer.py`

**Implementation notes:**
- Strictly judgment-only: no comments[], no ci_passed/validation gates.
- quality_gates are PR-holistic booleans (ID-066).

**References:** ID-023, ID-066.

**Acceptance criteria:**
- [ ] `comments` field absent
- [ ] `ci_passed` / `validation_profile_gates_satisfied` absent
- [ ] Validates conformant payloads
- [ ] ≥90% coverage

---

### SFP-17 [CONTRACTS] 🤖 — Test Designer output schema
**Labels:** manual-core, ai-agent, contracts | **Deps:** SFP-13

**Context:**
The Test Designer produces a deterministic test plan per PR-spec (ID-066).

**Requirements:**
- `TestDesignerOutput`: `{pr_spec_id, test_plan}`, where `test_plan ∈ {unit_tests, integration_tests, e2e_or_smoke_tests, negative_tests, edge_cases, regression_risks, required_validation_commands}`.

**Files to create/modify:**
- `packages/sfp-contracts/src/sfp_contracts/agents/test_designer.py`

**Implementation notes:**
- All test arrays are descriptions (strings), not code.
- required_validation_commands are the shell commands the gates run.

**References:** ID-066.

**Acceptance criteria:**
- [ ] Validates conformant payloads
- [ ] ≥90% coverage

---

### SFP-18 [CONTRACTS] 🤖 — Readiness evaluator output schema
**Labels:** manual-core, ai-agent, contracts | **Deps:** SFP-13

**Context:**
The Readiness Gate returns a verdict plus the gaps it found (ID-064/065).

**Requirements:**
- `ReadinessOutput`: `{ticket_id, verdict, blocking_ambiguities[], missing_inputs[], rubric_results}`.
- `verdict ∈ {READY, NEEDS_CLARIFICATION, MANUAL_REQUIRED}`.

**Files to create/modify:**
- `packages/sfp-contracts/src/sfp_contracts/agents/readiness.py`

**Implementation notes:**
- verdict drives routing: READY→plan, NEEDS_CLARIFICATION→user, MANUAL_REQUIRED→human (ID-065).
- missing_inputs lists unresolved context (ID-071).

**References:** ID-064, ID-065.

**Acceptance criteria:**
- [ ] All three verdicts covered
- [ ] Validates conformant payloads
- [ ] ≥90% coverage

---

### SFP-19 [CONTRACTS] 🤖 — Context-types catalogue (shared, versioned)
**Labels:** manual-core, ai-agent, contracts | **Deps:** SFP-6

**Context:**
Cross-ticket context outputs share a typed, versioned catalogue so names do not drift across tickets (ID-071).

**Requirements:**
- A versioned registry of context types (name + type), e.g. `repo_url:str`, `db_endpoint:str`, `db_secret_arn:secret_ref`, `aws_account_id:str`, `llm_provider_secret_ref:secret_ref`.
- A `secret_ref` marker type for secret outputs (value never stored).

**Files to create/modify:**
- `packages/sfp-contracts/src/sfp_contracts/context/types.py`

**Implementation notes:**
- Version the catalogue (schema_version) so additions don't break older tickets.
- secret_ref is a marker type — the value is never stored (ID-016).

**References:** ID-016, ID-071.

**Acceptance criteria:**
- [ ] Catalogue is versioned
- [ ] `secret_ref` type present
- [ ] ≥90% coverage

---

### SFP-20 [CONTRACTS] 🤖 — Ticket context I/O schemas (outputs/inputs)
**Labels:** manual-core, ai-agent, contracts | **Deps:** SFP-19

**Context:**
Each ticket declares its outputs and required inputs so the Readiness Gate can resolve dependencies deterministically (ID-070, ID-071).

**Requirements:**
- `TicketContextDeclaration{outputs:[(name, type)], required_inputs:[(name, source_ticket)]}`.
- Validation that declared types exist in the catalogue (SFP-19).

**Files to create/modify:**
- `packages/sfp-contracts/src/sfp_contracts/context/declaration.py`

**Implementation notes:**
- Validate every declared type against the SFP-19 catalogue at authoring time.
- This lets the Readiness Gate resolve dependencies deterministically.

**References:** ID-070, ID-071.

**Acceptance criteria:**
- [ ] Declaration validates against the catalogue
- [ ] Unknown type names rejected
- [ ] ≥90% coverage

---

### SFP-21 [CONTRACTS] 🤖 — Command contracts catalogue
**Labels:** manual-core, ai-agent, contracts | **Deps:** SFP-6

**Context:**
Cross-service commands are strongly typed platform contracts (MAS §5.3).

**Requirements:**
- Pydantic models for: `ExecuteCodingJob`, `SynchronizePullRequest`, `CancelCodingJob`, `ReviewPullRequest`, `CancelReviewJob`, `RequestUserInput`, `NotifyUser`, `RequestMerge`.
- Each carries the message envelope fields (`message_id`, `idempotency_key`, `correlation_id`, `causation_id`, `occurred_at`).

**Files to create/modify:**
- `packages/sfp-contracts/src/sfp_contracts/commands/*.py`

**Implementation notes:**
- Each command carries the envelope fields (ID-031); commands are point-to-point.
- Do not add GeneratePRSpecifications (internal Orchestrator, MAS §5.3).

**References:** MAS §5.3, ID-031.

**Acceptance criteria:**
- [ ] All 8 commands modelled
- [ ] Envelope fields present on each
- [ ] ≥90% coverage

---

### SFP-22 [CONTRACTS] 🤖 — Event contracts catalogue
**Labels:** manual-core, ai-agent, contracts | **Deps:** SFP-6

**Context:**
Platform events are strongly typed (MAS §5.4); Orchestrator-owned events are produced by the Orchestrator (ID-07).

**Requirements:**
- Event models: `ExternalEventReceived`, `TicketUpdated`, `PRSpecificationsUpdated`, `CodingJobUpdated`, `ReviewUpdated`, `UserInputReceived`, `UserInteractionUpdated`, `UserQueryReceived`, `MergeUpdated`, `DeploymentUpdated`, `WorkflowUpdated`.
- Envelope fields on each.

**Files to create/modify:**
- `packages/sfp-contracts/src/sfp_contracts/events/*.py`

**Implementation notes:**
- Events are pub-sub; Orchestrator produces TicketUpdated/PRSpecificationsUpdated/DeploymentUpdated/WorkflowUpdated (ID-072).
- Envelope fields on each.

**References:** MAS §5.4, ID-031, ID-072 (producer ownership).

**Acceptance criteria:**
- [ ] All events modelled
- [ ] Envelope fields present
- [ ] ≥90% coverage

---

### SFP-23 [CONTRACTS] 🤖 — ExternalEventReceived contract
**Labels:** manual-core, ai-agent, contracts | **Deps:** SFP-6

**Context:**
The single ingress contract wraps an authenticated external payload without interpreting it (MAS §5.5).

**Requirements:**
- `ExternalEventReceived{external_event_id, idempotency_key, received_at, provider, endpoint_id, headers, payload}`.
- `payload` remains opaque (not parsed by infrastructure).

**Files to create/modify:**
- `packages/sfp-contracts/src/sfp_contracts/events/external.py`

**Implementation notes:**
- payload stays opaque (bytes/str) — infrastructure never parses it.
- Owning services interpret via local schemas (ID-026, ID-041).

**References:** MAS §5.5, ID-031.

**Acceptance criteria:**
- [ ] Payload kept opaque
- [ ] Validates conformant payloads
- [ ] ≥90% coverage

---

### SFP-24 [CONTRACTS] 🤖 — Validation-profile enum + gate-mapping
**Labels:** manual-core, ai-agent, contracts | **Deps:** SFP-6

**Context:**
Each PR-spec carries a validation profile that determines its gates and whether human approval is required (ID-067, ID-024).

**Requirements:**
- `ValidationProfile` enum: `LEVEL_1_INTERNAL`, `LEVEL_2_BACKEND_OR_API`, `LEVEL_3_USER_FACING`, `LEVEL_4_HIGH_RISK`.
- A profile → required-gates mapping.
- Human-approval rule encoded: LEVEL_1 = no human approval (auto-merge eligible); LEVEL_2/3/4 = human approval required.

**Files to create/modify:**
- `packages/sfp-contracts/src/sfp_contracts/validation/profiles.py`

**Implementation notes:**
- Gate mapping is data, not code, so it can evolve without redeploys.
- LEVEL_1 = no human approval; LEVEL_2/3/4 require it (ID-024).

**References:** ID-024, ID-067.

**Acceptance criteria:**
- [ ] Enum and gate mapping present
- [ ] Human-approval rule encoded
- [ ] ≥90% coverage

---

## SHARED-FRAMEWORK Epic 🤖

### SFP-25 [SHARED-FW] 🤖 — sfp-messaging: Message Bus interface
**Labels:** manual-core, ai-agent, shared-fw | **Deps:** SFP-21, SFP-22, SFP-23, SFP-11

**Context:**
The Message Bus abstraction hides transport from business code (AP-010, Implementation Notes §1). The prod transport plugs in later (SFP-101); this ticket defines only the interface.

**Requirements:**
- Async `MessageBus` interface: `publish(message)`, `subscribe(handler)`.
- Transport-agnostic — no SNS/SQS/boto3 types leak.
- Accepts the typed contracts from SFP-21/22/23.

**Files to create/modify:**
- `packages/sfp-messaging/src/sfp_messaging/bus.py`
- `packages/sfp-messaging/tests/`

**Implementation notes:**
- Async first; no boto3/SNS/SQS types here (those belong to the SFP-101 transport).
- publish() accepts typed contracts, not dicts.

**References:** ID-052, MAS §4.5.

**Acceptance criteria:**
- [ ] Interface exposes publish/subscribe with no transport types
- [ ] ≥90% coverage

---

### SFP-26 [SHARED-FW] 🤖 — sfp-messaging: Handler + decorators
**Labels:** manual-core, ai-agent, shared-fw | **Deps:** SFP-25

**Context:**
Handlers are declared declaratively and auto-registered (Implementation Notes §1).

**Requirements:**
- `@command_handler(CommandType)` and `@event_handler(EventType)` decorators.
- A registry that maps message types → handlers.

**Files to create/modify:**
- `packages/sfp-messaging/src/sfp_messaging/{decorators.py,registry.py}`

**Implementation notes:**
- Decorators must never evaluate policy, mutate state, or hide business behaviour (Implementation Notes §1).

**References:** Implementation Notes §1, ID-052.

**Acceptance criteria:**
- [ ] Decorator registers a handler and routes messages by type
- [ ] Decorators perform no business logic
- [ ] ≥90% coverage

---

### SFP-27 [SHARED-FW] 🤖 — sfp-messaging: MessageContext
**Labels:** manual-core, ai-agent, shared-fw | **Deps:** SFP-25

**Context:**
Every handler receives a framework-provided MessageContext (Implementation Notes §1).

**Requirements:**
- `MessageContext{correlation_id, causation_id, message_id, received_at, retry_count, framework services}`.
- Constructed only by the framework.

**Files to create/modify:**
- `packages/sfp-messaging/src/sfp_messaging/context.py`

**Implementation notes:**
- Use contextvars so handlers don't thread it manually.
- Expose retry_count for idempotency/retry decisions.

**References:** Implementation Notes §1, ID-031.

**Acceptance criteria:**
- [ ] Context exposes all required fields; handlers never reconstruct it
- [ ] ≥90% coverage

---

### SFP-28 [SHARED-FW] 🤖 — sfp-messaging: envelope serde (JSON)
**Labels:** manual-core, ai-agent, shared-fw | **Deps:** SFP-13, SFP-25

**Context:**
Messages are transported in a typed envelope with idempotency/correlation/causation identifiers (ID-031).

**Requirements:**
- Serialize/deserialize the message envelope to/from JSON (ID-013).
- Preserve `idempotency_key`, `correlation_id`, `causation_id`, `message_id`.

**Files to create/modify:**
- `packages/sfp-messaging/src/sfp_messaging/envelope.py`

**Implementation notes:**
- JSON only (ID-013); idempotency_key is mandatory and distinct from message_id (ID-031).
- Round-trip must be lossless.

**References:** ID-013, ID-031.

**Acceptance criteria:**
- [ ] Round-trip preserves all envelope identifiers
- [ ] ≥90% coverage

---

### SFP-29 [SHARED-FW] 🤖 — sfp-messaging: in-memory transport
**Labels:** manual-core, ai-agent, shared-fw | **Deps:** SFP-25

**Context:**
Local/test transport so handlers run without AWS/SNS/SQS (Implementation Notes §1). Prod transport is SFP-101.

**Requirements:**
- `InMemoryTransport` implementing `MessageBus`.
- Synchronous in-process dispatch; records published messages for assertions.

**Files to create/modify:**
- `packages/sfp-messaging/src/sfp_messaging/transport/in_memory.py`

**Implementation notes:**
- Synchronous dispatch for deterministic tests; record every published message.
- Not for prod (SFP-101 is).

**References:** ID-052, ID-049.

**Acceptance criteria:**
- [ ] Publish dispatches to registered handlers in-process
- [ ] Published messages are inspectable for test assertions
- [ ] ≥90% coverage

---

### SFP-30 [SHARED-FW] 🤖 — sfp-observability: structlog setup + JSON
**Labels:** manual-core, ai-agent, shared-fw | **Deps:** SFP-11

**Context:**
v0 observability is structured logging only (ID-050).

**Requirements:**
- `structlog` configured for JSON output to stdout.
- A base logger factory.

**Files to create/modify:**
- `packages/sfp-observability/src/sfp_observability/logging.py`

**Implementation notes:**
- JSON to stdout so the ECS awslogs driver captures it (ID-050).
- Shared processor chain used by all services.

**References:** ID-050.

**Acceptance criteria:**
- [ ] Logs emitted as JSON to stdout
- [ ] ≥90% coverage

---

### SFP-31 [SHARED-FW] 🤖 — sfp-observability: correlation/causation binding
**Labels:** manual-core, ai-agent, shared-fw | **Deps:** SFP-30

**Context:**
Every log line must carry the workflow's correlation/causation identifiers (ID-031, ID-050).

**Requirements:**
- Contextvars-based binding of `correlation_id`/`causation_id` into every log line.

**Files to create/modify:**
- `packages/sfp-observability/src/sfp_observability/{context_vars.py,logging.py}`

**Implementation notes:**
- Bind via contextvars set by the messaging framework on consume; clear on exit.
- correlation_id propagates across the bus (ID-031).

**References:** ID-031, ID-050.

**Acceptance criteria:**
- [ ] Log lines include `correlation_id` when set in context
- [ ] ≥90% coverage

---

### SFP-32 [SHARED-FW] 🤖 — sfp-testing: in-memory bus fake
**Labels:** manual-core, ai-agent, shared-fw | **Deps:** SFP-29

**Context:**
Unit tests run handlers without AWS (ID-049, Implementation Notes §1).

**Requirements:**
- A testing fake of `MessageBus` (over SFP-29) with assertion helpers (`assert_published`, etc.).

**Files to create/modify:**
- `packages/sfp-testing/src/sfp_testing/bus.py`

**Implementation notes:**
- Thin wrapper over SFP-29 with assert helpers.
- Unit tests must run handlers with zero AWS (Implementation Notes §1).

**References:** ID-049.

**Acceptance criteria:**
- [ ] Handlers execute with no AWS dependency
- [ ] Assertion helpers present
- [ ] ≥90% coverage

---

### SFP-33 [SHARED-FW] 🤖 — sfp-testing: fake MessageContext + fixtures
**Labels:** manual-core, ai-agent, shared-fw | **Deps:** SFP-27

**Context:**
Tests need a framework-provided context without a real bus (ID-049).

**Requirements:**
- A `fake_context(...)` builder and pytest fixtures.

**Files to create/modify:**
- `packages/sfp-testing/src/sfp_testing/fixtures.py`

**Implementation notes:**
- Builder with sensible defaults; allow overriding correlation_id/retry_count in tests.

**References:** ID-049.

**Acceptance criteria:**
- [ ] Fixtures provide a usable MessageContext in tests
- [ ] ≥90% coverage

---

### SFP-34 [SHARED-FW] 🤖 — sfp-agent-runtime: abstraction interfaces
**Labels:** manual-core, ai-agent, shared-fw | **Deps:** SFP-11

**Context:**
The Agent Runtime is a vendor-independent abstraction (AP-010); the Claude SDK implements it (SFP-36).

**Requirements:**
- Interfaces for `AgentRuntime` (run an agent, return structured output) and `PromptProvider`.
- No reference to any vendor SDK.

**Files to create/modify:**
- `packages/sfp-agent-runtime/src/sfp_agent_runtime/interfaces.py`

**Implementation notes:**
- Protocol/ABC only; reference no vendor SDK.
- Lets SFP-36 swap implementations (Claude SDK now, others later) per AP-010.

**References:** AP-010, MAS §9.6.

**Acceptance criteria:**
- [ ] Interfaces reference no vendor SDK
- [ ] ≥90% coverage

---

### SFP-35 [SHARED-FW] 🤖 — sfp-agent-runtime: PromptBuilder
**Labels:** manual-core, ai-agent, shared-fw | **Deps:** SFP-34

**Context:**
Prompts are composed from Markdown fragments, not hardcoded (ID-059).

**Requirements:**
- `PromptBuilder` that composes a system prompt from a shared base + role + task fragments loaded from the `prompts/` tree.

**Files to create/modify:**
- `packages/sfp-agent-runtime/src/sfp_agent_runtime/prompt_builder.py`

**Implementation notes:**
- Load fragments from the prompts/ tree (ID-059); compose shared+role+task.
- Never inline prompt strings in agent code.

**References:** ID-059.

**Acceptance criteria:**
- [ ] Composes a full system prompt from fragments
- [ ] ≥90% coverage

---

## AGENT-LAYER Epic — the manual-core capability 🤖

### SFP-36 [AGENT] 🤖 — Agent Runtime impl (Claude Agent SDK wrapper)
**Labels:** manual-core, ai-agent, agent-layer | **Deps:** SFP-34, SFP-11 | **Context in:** `llm_provider_secret_ref`, `anthropic_base_url` (SFP-2)

**Context:**
The concrete runtime behind the SFP-34 abstraction, driving agents via the Claude Agent SDK against the configured Anthropic-compatible endpoint (ID-018).

**Requirements:**
- Implement `AgentRuntime` using `claude-agent-sdk`; resolve credentials/endpoint from config (SFP-11).
- Enforce per-role model selection (SFP-37).
- Validate every agent output against its JSON contract (SFP-13…18); reject non-conformant output.

**Files to create/modify:**
- `services/workspace-worker/src/workspace_worker/agent_runtime/runtime.py`
- entrypoints wiring

**Implementation notes:**
- Startup validation of model config (ID-020); no raw provider credentials.
- Unit-test with a stubbed SDK (no live calls in CI).

**References:** ID-018, ID-019, ID-020, ID-066.

**Acceptance criteria:**
- [ ] A stubbed agent call returns contract-valid JSON
- [ ] Non-conformant output is rejected
- [ ] ≥90% coverage

---

### SFP-37 [AGENT] 🤖 — Per-role model config routing
**Labels:** manual-core, ai-agent, agent-layer | **Deps:** SFP-36, SFP-11

**Context:**
Each role (Planner/Coder/Reviewer/Test Designer) uses a configurable model (ID-063).

**Requirements:**
- Read `SFP_AGENT_MODEL_PLANNER/CODER/REVIEWER` (+ `ANTHROPIC_BASE_URL`/auth) from config.
- Select the model per role; fail fast at startup if a role has no model.

**Files to create/modify:**
- `services/workspace-worker/src/workspace_worker/agent_runtime/model_config.py`

**Implementation notes:**
- Read SFP_AGENT_MODEL_* env via sfp-config; fail fast at startup if a role lacks a model (ID-020).
- Per-role defaults may be set.

**References:** ID-020, ID-063.

**Acceptance criteria:**
- [ ] Each role resolves a model from config
- [ ] Missing model → startup failure
- [ ] ≥90% coverage

---

### SFP-38 [AGENT] 🤖 — Repository Manager: clone
**Labels:** manual-core, ai-agent, agent-layer | **Deps:** SFP-6 | **Context in:** `repo_url` (SFP-1)

**Context:**
Agents operate on a local checkout of the target repo (ID-034).

**Requirements:**
- Clone the repo to a local path using `repo_url`; authenticate via the configured GitHub token.
- Idempotent (skip if already cloned).

**Files to create/modify:**
- `services/workspace-worker/src/workspace_worker/repo/manager.py`

**Implementation notes:**
- Clone with the injected token in the URL (never write the token to disk).
- Idempotent: skip if the worktree base exists.

**References:** ID-034, ID-035.

**Acceptance criteria:**
- [ ] Clones the repo using the injected token
- [ ] ≥90% coverage

---

### SFP-39 [AGENT] 🤖 — Repository Manager: worktree lifecycle
**Labels:** manual-core, ai-agent, agent-layer | **Deps:** SFP-38

**Context:**
Each job works in an ephemeral worktree (ID-033).

**Requirements:**
- Create and remove git worktrees per job.

**Files to create/modify:**
- `services/workspace-worker/src/workspace_worker/repo/worktree.py`

**Implementation notes:**
- One worktree per job under a temp base; remove on completion (ID-033).
- Never share worktrees across jobs.

**References:** ID-033.

**Acceptance criteria:**
- [ ] Worktree created and removed cleanly
- [ ] ≥90% coverage

---

### SFP-40 [AGENT] 🤖 — Repository Manager: branch lifecycle + cleanup
**Labels:** manual-core, ai-agent, agent-layer | **Deps:** SFP-38

**Context:**
Jobs need managed branches with cleanup (ID-034).

**Requirements:**
- Create/checkout/delete branches; cleanup on completion.

**Files to create/modify:**
- `services/workspace-worker/src/workspace_worker/repo/branch.py`

**Implementation notes:**
- Branch name includes the ticket id (ID-025).
- Delete local+remote branch on cleanup unless the PR is kept.

**References:** ID-034.

**Acceptance criteria:**
- [ ] Branch create/checkout/delete works; temp state cleaned up
- [ ] ≥90% coverage

---

### SFP-41 [AGENT] 🤖 — Git Provider Adapter: branch + push
**Labels:** manual-core, ai-agent, agent-layer | **Deps:** SFP-11 | **Context in:** `github_token_secret_ref`

**Context:**
Outbound GitHub operations go through an adapter (ID-035) using httpx + tenacity (ID-051).

**Requirements:**
- Push commits/branches via the GitHub API; token from config (no raw creds).
- Retry transient failures (tenacity).

**Files to create/modify:**
- `services/workspace-worker/src/workspace_worker/git/adapter.py`

**Implementation notes:**
- Use httpx (ID-051) with the token from config; retry with tenacity on 5xx/rate-limit.
- No raw git CLI credentials.

**References:** ID-035, ID-051.

**Acceptance criteria:**
- [ ] Push succeeds via API with the injected token (stubbed httpx)
- [ ] Retries on transient failure
- [ ] ≥90% coverage

---

### SFP-42 [AGENT] 🤖 — Git Provider Adapter: PR create/update
**Labels:** manual-core, ai-agent, agent-layer | **Deps:** SFP-41

**Context:**
PRs are the implementation review unit (ID-025).

**Requirements:**
- Create/update PRs via the GitHub API; include the Jira ticket reference in the PR body.

**Files to create/modify:**
- `services/workspace-worker/src/workspace_worker/git/pr.py`

**Implementation notes:**
- PR body references the Jira ticket (ID-025).
- Update the existing PR rather than creating duplicates on re-push.

**References:** ID-025, ID-035.

**Acceptance criteria:**
- [ ] PR created/updated; body references the ticket
- [ ] ≥90% coverage

---

### SFP-43 [AGENT] 🤖 — Git Provider Adapter: review submission
**Labels:** manual-core, ai-agent, agent-layer | **Deps:** SFP-41

**Context:**
The Reviewer submits its verdict to GitHub (ID-023).

**Requirements:**
- Submit PR reviews (`APPROVE` / `REQUEST_CHANGES`) via the GitHub API.

**Files to create/modify:**
- `services/workspace-worker/src/workspace_worker/git/review.py`

**Implementation notes:**
- Map review_status→GitHub event (APPROVE/REQUEST_CHANGES).
- Comments themselves live on GitHub (ID-066).

**References:** ID-023, ID-035.

**Acceptance criteria:**
- [ ] Review submitted with the correct event type
- [ ] ≥90% coverage

---

### SFP-44 [AGENT] 🤖 — Git Provider Adapter: branch synchronization
**Labels:** manual-core, ai-agent, agent-layer | **Deps:** SFP-41

**Context:**
PR branches must stay synchronized with base (ID-035, MAS §9.6).

**Requirements:**
- Update/synchronize a PR branch with its base via the GitHub API.

**Files to create/modify:**
- `services/workspace-worker/src/workspace_worker/git/sync.py`

**Implementation notes:**
- Trigger when base moves; handle merge conflicts as a normal failure (ID-068), not BLOCKED.

**References:** ID-035.

**Acceptance criteria:**
- [ ] Branch sync invoked correctly
- [ ] ≥90% coverage

---

### SFP-45 [AGENT] 🤖 — Local Execution Engine: build
**Labels:** manual-core, ai-agent, agent-layer | **Deps:** SFP-6

**Context:**
Generated code is built/tested inside the sandbox (ID-060).

**Requirements:**
- Run the project build inside the sandbox (SFP-48); capture pass/fail and logs.

**Files to create/modify:**
- `services/workspace-worker/src/workspace_worker/exec/build.py`

**Implementation notes:**
- Run inside the sandbox (SFP-48); capture exit code + tail of logs.
- Do not run on the host.

**References:** ID-060.

**Acceptance criteria:**
- [ ] Build runs in the container; result captured
- [ ] ≥90% coverage

---

### SFP-46 [AGENT] 🤖 — Local Execution Engine: test runner
**Labels:** manual-core, ai-agent, agent-layer | **Deps:** SFP-45

**Context:**
The coder must satisfy test requirements (ID-022, ID-039).

**Requirements:**
- Run pytest/unit tests in the sandbox; capture results + coverage.

**Files to create/modify:**
- `services/workspace-worker/src/workspace_worker/exec/tests.py`

**Implementation notes:**
- Invoke `uv run pytest --cov`; parse exit code + coverage.
- Coverage must meet the 90% gate (ID-049).

**References:** ID-039, ID-049, ID-060.

**Acceptance criteria:**
- [ ] Tests run; results and coverage parsed
- [ ] ≥90% coverage

---

### SFP-47 [AGENT] 🤖 — Local Execution Engine: linters/static analysis
**Labels:** manual-core, ai-agent, agent-layer | **Deps:** SFP-45

**Context:**
Lint/type checks run in the sandbox (ID-062).

**Requirements:**
- Run ruff and mypy in the sandbox; capture results.

**Files to create/modify:**
- `services/workspace-worker/src/workspace_worker/exec/lint.py`

**Implementation notes:**
- Run `ruff check` + `mypy` in the sandbox; parse output.
- Failures are normal development failures (ID-068).

**References:** ID-062, ID-060.

**Acceptance criteria:**
- [ ] ruff/mypy run; output parsed
- [ ] ≥90% coverage

---

### SFP-48 [AGENT] 🤖 — Sandbox: local container isolation
**Labels:** manual-core, ai-agent, agent-layer | **Deps:** SFP-45

**Context:**
Untrusted generated code runs isolated (ID-060).

**Requirements:**
- Run commands in an isolated local container: restricted FS, no default network egress, dropped capabilities, non-root, resource limits, teardown after the job.

**Files to create/modify:**
- `services/workspace-worker/src/workspace_worker/exec/sandbox.py`

**Implementation notes:**
- Drop all Linux capabilities, non-root, CPU/mem/time limits, read-only root + writable worktree only.
- Default-deny egress; allow only the Git Provider Adapter host (ID-060).

**References:** ID-060.

**Acceptance criteria:**
- [ ] Command runs isolated; egress blocked by default; container removed after
- [ ] ≥90% coverage

---

### SFP-49 [AGENT] 🤖 — Ticket context resolver: input resolution + injection
**Labels:** manual-core, ai-agent, agent-layer | **Deps:** SFP-20, SFP-19

**Context:**
Dependent tickets need facts produced by their dependencies (ID-071).

**Requirements:**
- Given a ticket's `required_inputs`, resolve values from completed dependencies' declared outputs.
- Inject resolved inputs into the PR spec / agent context; return the list of missing inputs.

**Files to create/modify:**
- `services/workspace-worker/src/workspace_worker/workflow/context_resolver.py`

**Implementation notes:**
- Resolve required_inputs by matching to completed dependencies' declared outputs (SFP-20/SFP-19).
- Inject as typed values; report missing inputs verbatim.

**References:** ID-071, ID-064.

**Acceptance criteria:**
- [ ] Resolves available inputs; returns the missing list when any are absent
- [ ] ≥90% coverage

---

### SFP-50 [AGENT] 🤖 — Readiness gate: rubric (rule-checks)
**Labels:** manual-core, ai-agent, agent-layer | **Deps:** SFP-24

**Context:**
Mechanically-checkable ticket completeness (ID-064, ID-070).

**Requirements:**
- Rule-checks for required ticket fields (objective, scope, acceptance criteria, testing requirements, dependencies, declared outputs/inputs).
- Missing required field → Not-Ready with a reason.

**Files to create/modify:**
- `services/workspace-worker/src/workspace_worker/workflow/readiness_rubric.py`

**Implementation notes:**
- Pure deterministic checks, no model; each missing field yields a specific reason.
- Runs before the evaluator (SFP-51).

**References:** ID-064, ID-070.

**Acceptance criteria:**
- [ ] Missing required field → Not-Ready with reason
- [ ] ≥90% coverage

---

### SFP-51 [AGENT] 🤖 — Readiness gate: evaluator + verdicts
**Labels:** manual-core, ai-agent, agent-layer | **Deps:** SFP-18, SFP-49, SFP-50

**Context:**
A model-based evaluator scores semantic dimensions and emits a verdict (ID-064).

**Requirements:**
- Evaluator (via SFP-36 runtime) scoring completeness/decomposability/unambiguity/testability.
- Returns `ReadinessOutput` (SFP-18): verdict ∈ {READY, NEEDS_CLARIFICATION, MANUAL_REQUIRED} + blocking_ambiguities + missing_inputs.

**Files to create/modify:**
- `services/workspace-worker/src/workspace_worker/workflow/readiness_gate.py`

**Implementation notes:**
- Model call via SFP-36 returning SFP-18; combine rubric results + semantic scoring.
- 'Zero blocking ambiguities' = READY.

**References:** ID-064, ID-065.

**Acceptance criteria:**
- [ ] Returns contract-valid `ReadinessOutput`
- [ ] Zero-blocking-ambiguity → READY
- [ ] ≥90% coverage

---

### SFP-52 [AGENT] 🤖 — Readiness gate: manual-required classification
**Labels:** manual-core, ai-agent, agent-layer | **Deps:** SFP-51

**Context:**
Some tickets cannot be executed by an agent (ID-065).

**Requirements:**
- Classify a ticket as `MANUAL_REQUIRED` when no agent can execute it (provisioning/account/secret tasks); route to a human, do not pipeline.

**Files to create/modify:**
- `services/workspace-worker/src/workspace_worker/workflow/manual_classifier.py`

**Implementation notes:**
- Detect provisioning-type tickets (accounts/secrets/domains/console) → MANUAL_REQUIRED (ID-065).
- Conservative: when unsure, prefer MANUAL.

**References:** ID-065.

**Acceptance criteria:**
- [ ] Provisioning-type ticket → MANUAL_REQUIRED
- [ ] ≥90% coverage

---

### SFP-53 [AGENT] 🤖 — Planner agent + prompt
**Labels:** manual-core, ai-agent, agent-layer | **Deps:** SFP-36, SFP-14, SFP-35, SFP-24

**Context:**
The Planner decomposes a ready ticket into PR-specs (ID-021).

**Requirements:**
- Planner agent: decompose a ready ticket into `pr_specs[]`; assign a `validation_profile`; declare risks.
- Return a contract-valid `PlannerOutput` (SFP-14).

**Files to create/modify:**
- `services/workspace-worker/prompts/planner/`
- `services/workspace-worker/src/workspace_worker/agents/planner.py`

**Implementation notes:**
- Decompose into PR-sized units; assign validation_profile (SFP-24); declare risks (ID-021).
- Never invent product requirements.

**References:** ID-021, ID-059, ID-066.

**Acceptance criteria:**
- [ ] Returns contract-valid `PlannerOutput` with ≥1 pr_spec
- [ ] ≥90% coverage

---

### SFP-54 [AGENT] 🤖 — Test Designer agent + prompt
**Labels:** manual-core, ai-agent, agent-layer | **Deps:** SFP-36, SFP-17, SFP-35

**Context:**
A deterministic test plan per PR-spec (ID-066).

**Requirements:**
- Produce a `test_plan` per pr_spec; return contract-valid `TestDesignerOutput` (SFP-17).

**Files to create/modify:**
- `services/workspace-worker/prompts/test_designer/`
- `services/workspace-worker/src/workspace_worker/agents/test_designer.py`

**Implementation notes:**
- Derive tests from acceptance criteria; include negative/edge cases.
- Output drives the Coder's test writing (ID-022).

**References:** ID-066.

**Acceptance criteria:**
- [ ] Returns contract-valid `TestDesignerOutput`
- [ ] ≥90% coverage

---

### SFP-55 [AGENT] 🤖 — Coder agent + prompt
**Labels:** manual-core, ai-agent, agent-layer | **Deps:** SFP-36, SFP-15, SFP-35, SFP-38, SFP-41, SFP-45

**Context:**
The Coder implements one PR-spec and submits it for review (ID-022).

**Requirements:**
- Implement one pr_spec; write/update tests; run build+tests (SFP-45/46); push + open PR (SFP-41/42).
- Return contract-valid `CoderOutput` (SFP-15).

**Files to create/modify:**
- `services/workspace-worker/prompts/coder/`
- `services/workspace-worker/src/workspace_worker/agents/coder.py`

**Implementation notes:**
- Implement only the assigned pr_spec scope (ID-022).
- Make assumptions explicit; never request user input mid-run (non-interactive, ID-032).

**References:** ID-022, ID-059.

**Acceptance criteria:**
- [ ] Returns contract-valid `CoderOutput` with a PR url
- [ ] ≥90% coverage

---

### SFP-56 [AGENT] 🤖 — Reviewer agent + prompt (judgment-only)
**Labels:** manual-core, ai-agent, agent-layer | **Deps:** SFP-36, SFP-16, SFP-35, SFP-42

**Context:**
The Reviewer returns holistic judgments; comments live on GitHub (ID-023, ID-066).

**Requirements:**
- Review the PR; emit judgment-only `ReviewerOutput` (SFP-16); submit the review to GitHub (SFP-43).

**Files to create/modify:**
- `services/workspace-worker/prompts/reviewer/`
- `services/workspace-worker/src/workspace_worker/agents/reviewer.py`

**Implementation notes:**
- Holistic PR-level judgments only; submit comments to GitHub (ID-023).
- Do not approve if any blocking quality_gate is false.

**References:** ID-023, ID-066.

**Acceptance criteria:**
- [ ] Returns contract-valid `ReviewerOutput` (no comments field)
- [ ] ≥90% coverage

---

### SFP-57 [AGENT] 🤖 — Validation profile logic: assignment + gate enforcement
**Labels:** manual-core, ai-agent, agent-layer | **Deps:** SFP-24

**Context:**
Validation profiles determine gates and the human-approval requirement (ID-024, ID-067).

**Requirements:**
- Map `validation_profile` → required gates.
- Enforce LEVEL_1 = no human approval (auto-merge eligible); LEVEL_2/3/4 = human approval required.

**Files to create/modify:**
- `services/workspace-worker/src/workspace_worker/workflow/validation.py`

**Implementation notes:**
- Map profile→gates (SFP-24).
- Enforce LEVEL_1 auto-merge eligibility vs LEVEL_2+ human approval (ID-024).

**References:** ID-024, ID-067.

**Acceptance criteria:**
- [ ] Profile → gate mapping correct; human-approval rule enforced
- [ ] ≥90% coverage

---

### SFP-58 [AGENT] 🤖 — Failure classification logic
**Labels:** manual-core, ai-agent, agent-layer | **Deps:** SFP-13

**Context:**
Crisp failure classification avoids noise and recognizes rework as normal (ID-068).

**Requirements:**
- Classify outcomes: normal rework (`CHANGES_REQUESTED`, not a failure), development failures (lint/test/build/CI), BLOCKED (external intervention only).

**Files to create/modify:**
- `services/workspace-worker/src/workspace_worker/workflow/failure.py`

**Implementation notes:**
- CHANGES_REQUESTED is normal rework, not failure (ID-068).
- BLOCKED only for external-intervention causes.

**References:** ID-068.

**Acceptance criteria:**
- [ ] `CHANGES_REQUESTED` classified as normal rework, not failure
- [ ] BLOCKED only for external-intervention causes
- [ ] ≥90% coverage

---

## LOCAL-DEV-RUNBOOK Epic 🤖

### SFP-59 [LOCAL] 🤖 — docker compose: Postgres (multi-DB)
**Labels:** manual-core, ai-agent, local-dev | **Deps:** SFP-6

**Context:**
Local dev needs a Postgres with the logical databases the services will use (ID-055, MAS §10.14).

**Requirements:**
- A compose service running Postgres that creates the logical DBs (`identity`, `orchestrator`, `communication`, `external_events`) at startup.

**Files to create/modify:**
- `infrastructure/local/compose.yaml`
- `infrastructure/local/postgres/init.sh`

**Implementation notes:**
- Official postgres image; create logical DBs via a script in /docker-entrypoint-initdb.d.
- One cluster, multiple DBs (MAS §10.14).

**References:** ID-055, MAS §10.14.

**Acceptance criteria:**
- [ ] `docker compose up` starts Postgres with all logical DBs
- [ ] ≥90% coverage on any helper code

---

### SFP-60 [LOCAL] 🤖 — docker compose: LocalStack (SNS/SQS/DLQ)
**Labels:** manual-core, ai-agent, local-dev | **Deps:** SFP-59

**Context:**
Local dev emulates the messaging surface without AWS (ID-054).

**Requirements:**
- A LocalStack (Community) service emulating SNS/SQS/DLQ, wired via `AWS_ENDPOINT_URL`.

**Files to create/modify:**
- `infrastructure/local/compose.yaml` (localstack service)

**Implementation notes:**
- Community image is enough (SNS/SQS/DLQ); set AWS_ENDPOINT_URL=http://localstack:4566.
- No Secrets Manager emulation (ID-054).

**References:** ID-054.

**Acceptance criteria:**
- [ ] LocalStack responds on `localhost:4566`; SNS/SQS API calls succeed
- [ ] ≥90% coverage on any helper code

---

### SFP-61 [LOCAL] 🤖 — docker compose: OTel collector (dev sink)
**Labels:** manual-core, ai-agent, local-dev | **Deps:** SFP-59

**Context:**
Dev telemetry needs a local sink (ID-050).

**Requirements:**
- An OTel Collector container as the dev telemetry sink (logs to console).

**Files to create/modify:**
- `infrastructure/local/compose.yaml` (collector service)

**Implementation notes:**
- Minimal OTel collector receiving OTLP, logging to console.
- Dev-only; prod uses CloudWatch (ID-050).

**References:** ID-050.

**Acceptance criteria:**
- [ ] Collector container starts and receives dev telemetry
- [ ] ≥90% coverage on any helper code

---

### SFP-62 [LOCAL] 🤖 — Compose init: local provisioning + Alembic baseline
**Labels:** manual-core, ai-agent, local-dev | **Deps:** SFP-60

**Context:**
`docker compose up` should yield a fully-provisioned local environment (ID-054/055).

**Requirements:**
- An init step that applies the Pulumi program against LocalStack (topics/queues) and runs Alembic baseline against each logical DB on startup.

**Files to create/modify:**
- `infrastructure/local/init/`

**Implementation notes:**
- One-shot container running `pulumi up` against LocalStack + `alembic upgrade head` per logical DB.
- Idempotent.

**References:** ID-054, ID-055, ID-014.

**Acceptance criteria:**
- [ ] `docker compose up` yields topics/queues + migrated DBs with no manual steps
- [ ] ≥90% coverage on any helper code

---

### SFP-63 [LOCAL] 🤖 — Manual-run runbook
**Labels:** manual-core, ai-agent, local-dev | **Deps:** SFP-49, SFP-51, SFP-53, SFP-54, SFP-55, SFP-56, SFP-57

**Context:**
This is the bootstrap vehicle: a human plays the Orchestrator end-to-end using the Manual-Core components, to validate the agent layer before the platform is built.

**Requirements:**
- A runbook stepping through: pick an unblocked ticket → resolve context (SFP-49) → readiness gate (SFP-51) → planner (SFP-53) → test design (SFP-54) → coder (SFP-55) → reviewer (SFP-56) → validation/human-approval (SFP-57) → merge.
- The exact commands to run each component locally.

**Files to create/modify:**
- `docs/manual-run-runbook.md`

**Implementation notes:**
- Step-by-step with exact `uv run` commands per component.
- Validate one ticket end-to-end before scaling (the bootstrap vehicle, ID-072).

**References:** ID-063, ID-064, ID-066, ID-070, ID-072.

**Acceptance criteria:**
- [ ] Runbook covers the full loop with runnable commands
- [ ] A dry-run of one Manual-Core ticket succeeds following the runbook

---

# PLATFORM — depends on Manual Core (label: `platform`)

## PREREQ Epic — Manual prerequisites · Platform 👤

### SFP-64 [PREREQ] 👤 — AWS account + billing (eu-west-1)
**Labels:** platform, manual, prereq | **Deps:** — | **Context out:** `aws_account_id`, `region`

**Context:**
The platform runs on AWS in region eu-west-1 (ID-015, ID-072). Required before any Pulumi/infra.

**Human action required:** Account creation requires billing and identity verification.

**What the human must do:**
1. Create an AWS account and set up billing.
2. Select region eu-west-1.
3. Configure a budget alert.

**Verification:**
- [ ] AWS account active; default region eu-west-1
- [ ] Budget alert configured

**References:** ID-015, ID-072.

---

### SFP-65 [PREREQ] 👤 — IAM user for Pulumi
**Labels:** platform, manual, prereq | **Deps:** SFP-64 | **Context out:** `pulumi_iam_secret_ref`

**Context:**
Pulumi provisions infrastructure non-interactively (ID-014) and needs programmatic credentials.

**Human action required:** IAM user creation and key custody.

**What the human must do:**
1. Create an IAM user `pulumi` with programmatic access.
2. Attach a least-privilege policy sufficient for the platform resources.
3. Store Access Key ID + Secret Access Key as a secret reference.

**Verification:**
- [ ] IAM user exists; key stored as a secret reference

**References:** ID-014, ID-016.

---

### SFP-66 [PREREQ] 👤 — Pulumi bootstrap (state backend + stack config)
**Labels:** platform, manual, prereq | **Deps:** SFP-65 | **Context out:** `pulumi_state_backend`, `stack_config`

**Context:**
Pulumi needs a state backend and project/stack structure (ID-014).

**Human action required:** Backend setup.

**What the human must do:**
1. Choose a state backend (Pulumi Cloud or S3).
2. Create the Pulumi project for SFP with `dev` and `prod` stacks.
3. Configure region eu-west-1 and the Pulumi IAM credentials.

**Verification:**
- [ ] `pulumi stack ls` works
- [ ] `dev` and `prod` stacks initialized

**References:** ID-014.

---

### SFP-67 [PREREQ] 👤 — Integration secrets in Secrets Manager
**Labels:** platform, manual, prereq | **Deps:** SFP-64 | **Context out:** `github_token_secret_ref`, `slack_token_secret_ref`, `jira_token_secret_ref`, `llm_provider_secret_ref`

**Context:**
Services need provider/runtime secrets stored centrally (ID-016).

**Human action required:** Secret creation and value custody.

**What the human must do:**
1. Create Secrets Manager secrets for: GitHub token, Slack token, Jira token, LLM provider key.
2. Store each value; record the secret ARN/ID (values never committed).

**Verification:**
- [ ] Secrets exist; ARNs recorded; values not in the repo

**References:** ID-016.

---

### SFP-68 [PREREQ] 👤 — Domain/DNS
**Labels:** platform, manual, prereq | **Deps:** SFP-64 | **Context out:** `domain_name`, `hosted_zone`

**Context:**
External ingress (webhooks, API) needs a domain and hosted zone (ID-028).

**Human action required:** Domain registration and delegation.

**What the human must do:**
1. Register a domain (or use an existing one).
2. Create a Route53 hosted zone and delegate nameservers.

**Verification:**
- [ ] Domain registered; hosted zone created; NS records delegated

**References:** ID-028.

---

### SFP-69 [PREREQ] 👤 — Slack workspace + app credentials
**Labels:** platform, manual, prereq | **Deps:** SFP-64 | **Context out:** `slack_bot_token_secret_ref`, `slack_signing_secret_ref`

**Context:**
Slack is the v0 communication provider (ID-027); a Slack app is required.

**Human action required:** Slack app creation.

**What the human must do:**
1. Create a Slack app in the workspace.
2. Enable bot token scopes and event subscriptions.
3. Store the bot token and signing secret as secret references.

**Verification:**
- [ ] App installed; bot token + signing secret stored as refs

**References:** ID-027, ID-029.

---

## PLATFORM-INFRA Epic — Pulumi 🤖

### SFP-70 [PLAT-INFRA] 🤖 — VPC + subnets
**Labels:** platform, ai-agent, plat-infra | **Deps:** SFP-66 | **Context out:** `vpc_id`, `subnet_ids`

**Context:**
All platform resources live in a VPC (MAS §10.5).

**Requirements:**
- VPC with public and private subnets across ≥2 AZs in eu-west-1.

**Files to create/modify:**
- `infrastructure/platform/vpc.py`

**Implementation notes:**
- eu-west-1, ≥2 AZs for HA. Private subnets for services/DB, public for ALB/NAT. Export vpc_id+subnet_ids.

**References:** MAS §10.5.

**Acceptance criteria:**
- [ ] VPC + subnets created in eu-west-1
- [ ] `pulumi up` succeeds

---

### SFP-71 [PLAT-INFRA] 🤖 — NAT gateway + routing
**Labels:** platform, ai-agent, plat-infra | **Deps:** SFP-70 | **Context out:** `nat_gateway_id`

**Context:**
Private subnets need controlled egress (MAS §10.5).

**Requirements:**
- NAT gateway for private-subnet egress; route tables.

**Files to create/modify:**
- `infrastructure/platform/nat.py`

**Implementation notes:**
- One NAT in a public subnet; private route tables default via NAT. ~$32/mo/AZ — keep one for dev.

**References:** MAS §10.5.

**Acceptance criteria:**
- [ ] Private subnets route egress via NAT
- [ ] `pulumi up` succeeds

---

### SFP-72 [PLAT-INFRA] 🤖 — Security groups
**Labels:** platform, ai-agent, plat-infra | **Deps:** SFP-70

**Context:**
Security boundaries between ALB, services, and DB (MAS §10.5/§11).

**Requirements:**
- Security groups for ALB, services, and DB with least-privilege rules.

**Files to create/modify:**
- `infrastructure/platform/security.py`

**Implementation notes:**
- Least-privilege: ALB→services (app port), services→DB (5432), services→SQS/SNS/Secrets over HTTPS. No 0.0.0.0/0 inbound except the ALB.

**References:** MAS §10.5.

**Acceptance criteria:**
- [ ] Security groups created with documented rules
- [ ] `pulumi up` succeeds

---

### SFP-73 [PLAT-INFRA] 🤖 — Aurora Serverless PostgreSQL (multi-DB)
**Labels:** platform, ai-agent, plat-infra | **Deps:** SFP-70, SFP-67 | **Context out:** `db_endpoint`, `db_secret_arn`

**Context:**
PostgreSQL is the persistence technology (ID-005); one cluster hosts logical DBs (MAS §10.14).

**Requirements:**
- Aurora Serverless v2 PostgreSQL in private subnets; credentials in Secrets Manager.
- Logical databases for each service created at provisioning.

**Files to create/modify:**
- `infrastructure/platform/database.py`

**Implementation notes:**
- Serverless v2; low min_capacity to save dev cost; secret in Secrets Manager. Create logical DBs per service via a post-create script.

**References:** ID-005, MAS §10.14.

**Acceptance criteria:**
- [ ] Cluster created; endpoint and secret ARN exported
- [ ] Logical databases present

---

### SFP-74 [PLAT-INFRA] 🤖 — ECS cluster (Fargate)
**Labels:** platform, ai-agent, plat-infra | **Deps:** SFP-70 | **Context out:** `ecs_cluster_arn`

**Context:**
Long-running services run on ECS Fargate (ID-015).

**Requirements:**
- An ECS cluster (Fargate) for the four long-running services.

**Files to create/modify:**
- `infrastructure/platform/ecs.py`

**Implementation notes:**
- Fargate (no EC2 to manage). Long-running services run as ECS services.

**References:** ID-015.

**Acceptance criteria:**
- [ ] ECS cluster created
- [ ] `pulumi up` succeeds

---

### SFP-75 [PLAT-INFRA] 🤖 — ECR registries (per service)
**Labels:** platform, ai-agent, plat-infra | **Deps:** SFP-74 | **Context out:** `ecr_repo_urls`

**Context:**
Each service image is pushed to its own ECR repo (ID-015).

**Requirements:**
- One ECR repository per service.

**Files to create/modify:**
- `infrastructure/platform/ecr.py`

**Implementation notes:**
- One repo per service; scan-on-push; lifecycle policy to prune untagged images.

**References:** ID-015.

**Acceptance criteria:**
- [ ] Repositories created for each service

---

### SFP-76 [PLAT-INFRA] 🤖 — AWS Batch compute env + job queue + max-vCpus
**Labels:** platform, ai-agent, plat-infra | **Deps:** SFP-74 | **Context out:** `batch_compute_env_arn`, `job_queue_arn`

**Context:**
The Workspace Worker executes one ephemeral task per job on Batch (ID-060/061).

**Requirements:**
- A Fargate Batch compute environment + job queue.
- `max-vCpus` ceiling as a token/compute cap (ID-061); scale-to-zero when idle.

**Files to create/modify:**
- `infrastructure/platform/batch.py`

**Implementation notes:**
- Fargate compute env; max-vCpus = token/compute ceiling (ID-061); scale to 0 when idle.

**References:** ID-015, ID-060, ID-061.

**Acceptance criteria:**
- [ ] Compute environment + queue created with `max-vCpus` set
- [ ] Scales to zero when idle (verified at runtime)

---

### SFP-77 [PLAT-INFRA] 🤖 — IAM roles + policies (per service)
**Labels:** platform, ai-agent, plat-infra | **Deps:** SFP-70

**Context:**
Each service runs under least-privilege roles (MAS §10.7).

**Requirements:**
- Per-service execution and task roles; access scoped to that service's queues/topics/DB/secrets.

**Files to create/modify:**
- `infrastructure/platform/iam.py`

**Implementation notes:**
- One execution role + task role per service; scope to that service's queues/topics/DB/secrets only (AP-001).

**References:** MAS §10.7.

**Acceptance criteria:**
- [ ] Roles/policies created per service
- [ ] `pulumi up` succeeds

---

### SFP-78 [PLAT-INFRA] 🤖 — Secrets Manager + config injection wiring
**Labels:** platform, ai-agent, plat-infra | **Deps:** SFP-67

**Context:**
Services resolve secrets/config centrally (ID-016).

**Requirements:**
- Wire service tasks to load secrets via `sfp-config`; IAM permission to read only their own secrets.

**Files to create/modify:**
- `infrastructure/platform/secrets.py`

**Implementation notes:**
- Services load secrets via sfp-config at runtime (ID-016); task role grants read only to its own secrets.

**References:** ID-016.

**Acceptance criteria:**
- [ ] Services resolve their secrets at runtime
- [ ] `pulumi up` succeeds

---

### SFP-79 [PLAT-INFRA] 🤖 — CloudWatch Logs groups + shipping
**Labels:** platform, ai-agent, plat-infra | **Deps:** SFP-70

**Context:**
Structured logs ship to CloudWatch Logs (ID-050).

**Requirements:**
- Log groups per service with retention; ECS tasks ship stdout to CloudWatch Logs.

**Files to create/modify:**
- `infrastructure/platform/logs.py`

**Implementation notes:**
- One log group per service with retention; awslogs driver on tasks; correlation_id in JSON lines (ID-050).

**References:** ID-050.

**Acceptance criteria:**
- [ ] Log groups created; service logs visible in CloudWatch Logs

---

### SFP-80 [PLAT-INFRA] 🤖 — Alarms + dashboards
**Labels:** platform, ai-agent, plat-infra | **Deps:** SFP-79

**Context:**
Operational visibility on platform health (MAS §10.11).

**Requirements:**
- Basic alarms (error rates, DLQ depth, queue backlog) and a dashboard.

**Files to create/modify:**
- `infrastructure/platform/observability.py`

**Implementation notes:**
- Alarms: DLQ depth, queue backlog, error rates, Batch failed jobs. Dashboard per service group.

**References:** MAS §10.11.

**Acceptance criteria:**
- [ ] Alarms + dashboard created

---

### SFP-81 [PLAT-INFRA] 🤖 — DNS + TLS (Route53 + ACM)
**Labels:** platform, ai-agent, plat-infra | **Deps:** SFP-68, SFP-70 | **Context out:** `api_domain`

**Context:**
External ingress needs DNS and TLS (MAS §10.5, ID-028).

**Requirements:**
- ACM certificate for the domain; Route53 records for API/webhooks; TLS termination.

**Files to create/modify:**
- `infrastructure/platform/dns.py`

**Implementation notes:**
- ACM cert validated via Route53 DNS; ALB listener with TLS; records for api + webhooks subdomains.

**References:** MAS §10.5, ID-028.

**Acceptance criteria:**
- [ ] Certificate issued; DNS records resolve; TLS works

---

## PERSISTENCE Epic 🤖

### SFP-82 [PERSIST] 🤖 — Orchestrator DB: Alembic + Base + business/operational schemas
**Labels:** platform, ai-agent, persistence | **Deps:** SFP-6, SFP-13 | **Context in:** `db_endpoint`, `db_secret_arn` (SFP-73)

**Context:**
The Orchestrator owns authoritative business state (MAS §7.4), persisted with `business` + `operational` schemas (ID-058) and Alembic migrations (ID-008).

**Requirements:**
- SQLAlchemy `Base`; `business` and `operational` Postgres schemas.
- Alembic environment wired to the Orchestrator DB (async engine).

**Files to create/modify:**
- `services/orchestrator/src/orchestrator/infrastructure/persistence/{base.py,migrations/}`
- `services/orchestrator/alembic.ini`

**Implementation notes:**
- Async SQLAlchemy + asyncpg. Two Postgres schemas: business + operational (ID-058). Alembic env.py imports all models for autogenerate.

**References:** ID-005, ID-008, ID-058.

**Acceptance criteria:**
- [ ] Alembic baseline runs against the Orchestrator DB
- [ ] `business` + `operational` schemas created

---

### SFP-83 [PERSIST] 🤖 — `Project` + `ProjectUser` models
**Labels:** platform, ai-agent, persistence | **Deps:** SFP-82

**Context:**
Project is the v0 domain boundary; ProjectUser links users to projects (MAS §6.4/§6.10).

**Requirements:**
- `Project` and `ProjectUser` ORM models in the `business` schema; relationships per MAS §6.4 (Project 1—* ProjectUser).
- Cross-service references (e.g. `user_id`) are plain ID columns, not foreign keys (ID-058).

**Files to create/modify:**
- `services/orchestrator/src/orchestrator/infrastructure/persistence/models/{project.py,project_user.py}`

**Implementation notes:**
- business schema. project_user links project_id + user_id (no cross-service FK to identity).

**References:** ID-058, MAS §6.4, §6.10.

**Acceptance criteria:**
- [ ] Models created; migration autogenerates the tables
- [ ] ≥90% coverage

---

### SFP-84 [PERSIST] 🤖 — `Ticket` model
**Labels:** platform, ai-agent, persistence | **Deps:** SFP-82

**Context:**
Ticket is the unit of work sourced from Jira (MAS §6.9).

**Requirements:**
- `Ticket` model (`business` schema): immutable `ticket_id`, `project_id`, workflow `status`, external reference.

**Files to create/modify:**
- `.../persistence/models/ticket.py`

**Implementation notes:**
- business schema; status is the workflow enum (MAS §8.4); external_ref holds the Jira key.

**References:** MAS §6.9, ID-058.

**Acceptance criteria:**
- [ ] Model + migration present
- [ ] ≥90% coverage

---

### SFP-85 [PERSIST] 🤖 — `PRSpecification` model
**Labels:** platform, ai-agent, persistence | **Deps:** SFP-82

**Context:**
A PRSpecification is immutable implementation intent after LOCKED (MAS §6.9, ID-021).

**Requirements:**
- `PRSpecification` model: `pr_specification_id`, `ticket_id`, JSON `payload` (the Planner output), `validation_profile`, `locked` flag.

**Files to create/modify:**
- `.../persistence/models/pr_specification.py`

**Implementation notes:**
- business schema; payload JSON stores the full PlannerOutput pr_spec; locked bool enforces immutability after LOCKED (MAS §6.9).

**References:** MAS §6.9, ID-021, ID-067.

**Acceptance criteria:**
- [ ] Model + migration present; immutability after LOCKED enforced
- [ ] ≥90% coverage

---

### SFP-86 [PERSIST] 🤖 — `CodingJob` model
**Labels:** platform, ai-agent, persistence | **Deps:** SFP-82

**Context:**
A CodingJob is one implementation execution (MAS §6.9).

**Requirements:**
- `CodingJob` model: `coding_job_id`, `pr_specification_id`, `status`, `branch`, `pull_request_url`.

**Files to create/modify:**
- `.../persistence/models/coding_job.py`

**Implementation notes:**
- business schema; one CodingJob per locked PRSpecification (1:1 in v0).

**References:** MAS §6.9, ID-022.

**Acceptance criteria:**
- [ ] Model + migration present
- [ ] ≥90% coverage

---

### SFP-87 [PERSIST] 🤖 — `Review` model
**Labels:** platform, ai-agent, persistence | **Deps:** SFP-82

**Context:**
A Review is a single review iteration for a CodingJob; immutable once completed (MAS §6.9 v0.1.2).

**Requirements:**
- `Review` model: `review_id`, `coding_job_id`, `review_status`, JSON `quality_gates`, `iteration`.

**Files to create/modify:**
- `.../persistence/models/review.py`

**Implementation notes:**
- business schema; iteration int; many per CodingJob (MAS §6.9 v0.1.2); immutable once completed.

**References:** MAS §6.9, ID-023.

**Acceptance criteria:**
- [ ] Model + migration present; multiple Reviews per CodingJob supported
- [ ] ≥90% coverage

---

### SFP-88 [PERSIST] 🤖 — `Merge` model
**Labels:** platform, ai-agent, persistence | **Deps:** SFP-82

**Context:**
Merge execution for a CodingJob (MAS §6.9).

**Requirements:**
- `Merge` model: `merge_id`, `coding_job_id`, `status`.

**Files to create/modify:**
- `.../persistence/models/merge.py`

**Implementation notes:**
- business schema; belongs to CodingJob.

**References:** MAS §6.9.

**Acceptance criteria:**
- [ ] Model + migration present
- [ ] ≥90% coverage

---

### SFP-89 [PERSIST] 🤖 — `Deployment` model
**Labels:** platform, ai-agent, persistence | **Deps:** SFP-82

**Context:**
Deployment observation for a merged CodingJob (MAS §6.9).

**Requirements:**
- `Deployment` model: `deployment_id`, `merge_id`, `status`.

**Files to create/modify:**
- `.../persistence/models/deployment.py`

**Implementation notes:**
- business schema; related to Merge.

**References:** MAS §6.9.

**Acceptance criteria:**
- [ ] Model + migration present
- [ ] ≥90% coverage

---

### SFP-90 [PERSIST] 🤖 — `UserDecision` model
**Labels:** platform, ai-agent, persistence | **Deps:** SFP-82

**Context:**
A UserDecision is the durable, confirmed outcome of a UserInteraction (MAS §6.9, ID-069).

**Requirements:**
- `UserDecision` model: `user_decision_id`, `interaction_id` (reference, no cross-service FK), `decision`, `confirmed_by`, `confirmed_at`. Immutable.

**Files to create/modify:**
- `.../persistence/models/user_decision.py`

**Implementation notes:**
- business schema; interaction_id is a plain reference (no FK to communication); immutable (AP-005).

**References:** MAS §6.9, ID-024, ID-069.

**Acceptance criteria:**
- [ ] Model + migration present; immutability enforced
- [ ] ≥90% coverage

---

### SFP-91 [PERSIST] 🤖 — `WorkflowDecision` model
**Labels:** platform, ai-agent, persistence | **Deps:** SFP-82

**Context:**
Every workflow-affecting transition records an immutable WorkflowDecision (MAS §8.5, ID-072).

**Requirements:**
- `WorkflowDecision` model: `workflow_decision_id`, `evaluated_policy`, JSON `inputs`, `resulting_state`, JSON `emitted_commands`.

**Files to create/modify:**
- `.../persistence/models/workflow_decision.py`

**Implementation notes:**
- business schema; JSON columns for inputs/emitted_commands; immutable audit record (MAS §8.5).

**References:** MAS §8.5, ID-072.

**Acceptance criteria:**
- [ ] Model + migration present; immutability enforced
- [ ] ≥90% coverage

---

### SFP-92 [PERSIST] 🤖 — Transactional outbox table + relay publisher
**Labels:** platform, ai-agent, persistence | **Deps:** SFP-82

**Context:**
Atomic persist-and-publish guarantees reliable outbound messaging (ID-053).

**Requirements:**
- Outbox table (`operational` schema): `id`, `message_type`, JSON `payload`, `idempotency_key`, `created_at`, `published_at`.
- A relay publisher that claims unpublished rows with `FOR UPDATE SKIP LOCKED`, publishes to the Message Bus, and marks `published_at`.

**Files to create/modify:**
- `.../persistence/outbox.py`
- `.../messaging/outbox_relay.py`

**Implementation notes:**
- operational schema; relay claims rows with FOR UPDATE SKIP LOCKED (ID-053); publish after txn commit; mark published_at.

**References:** ID-053, ID-011.

**Acceptance criteria:**
- [ ] Outbox row written in the same transaction as the business-state change
- [ ] Relay claims/publishes/marks with `SKIP LOCKED`
- [ ] ≥90% coverage

---

### SFP-93 [PERSIST] 🤖 — Idempotency / message ledger
**Labels:** platform, ai-agent, persistence | **Deps:** SFP-82

**Context:**
At-least-once delivery requires per-service idempotency on `idempotency_key` (ID-011).

**Requirements:**
- Message-ledger table (`operational` schema) keyed on `idempotency_key`.
- Dedup check used in handlers.

**Files to create/modify:**
- `.../persistence/idempotency.py`

**Implementation notes:**
- operational schema; keyed on idempotency_key; checked in every handler (ID-011).

**References:** ID-011, MAS §4.10.

**Acceptance criteria:**
- [ ] Replaying the same `idempotency_key` produces no duplicate business effect
- [ ] ≥90% coverage

---

### SFP-94 [PERSIST] 🤖 — Identity DB: Base + `User` + `UserExternalIdentity`
**Labels:** platform, ai-agent, persistence | **Deps:** SFP-6 | **Context in:** `db_endpoint`, `db_secret_arn`

**Context:**
Identity owns User/UserExternalIdentity (MAS §9.3).

**Requirements:**
- Identity DB Base + Alembic; `User` and `UserExternalIdentity` models (`business` schema).

**Files to create/modify:**
- `services/identity/src/identity/infrastructure/persistence/{base.py,migrations/,models/}`

**Implementation notes:**
- business schema; user_id immutable; external identities reference provider + provider_user_id.

**References:** ID-058, MAS §9.3.

**Acceptance criteria:**
- [ ] Alembic baseline + models present
- [ ] ≥90% coverage

---

### SFP-95 [PERSIST] 🤖 — Communication DB: `UserInteraction`
**Labels:** platform, ai-agent, persistence | **Deps:** SFP-6 | **Context in:** `db_endpoint`, `db_secret_arn`

**Context:**
Communication owns the UserInteraction lifecycle (MAS §9.4).

**Requirements:**
- Communication DB Base + Alembic; `UserInteraction` model (incl. summary, `expires_at`, `last_message_*`).

**Files to create/modify:**
- `services/communication/src/communication/infrastructure/persistence/`

**Implementation notes:**
- operational schema; UserInteraction holds summary (never transcripts, AP-009); expires_at drives the 8h timer.

**References:** ID-058, MAS §9.4.

**Acceptance criteria:**
- [ ] Alembic baseline + model present
- [ ] ≥90% coverage

---

### SFP-96 [PERSIST] 🤖 — External Events DB: endpoint config
**Labels:** platform, ai-agent, persistence | **Deps:** SFP-6 | **Context in:** `db_endpoint`, `db_secret_arn`

**Context:**
External Events owns endpoint configuration (MAS §9.2).

**Requirements:**
- External Events DB Base + Alembic; `EndpointConfig` model (provider, auth strategy, secret reference, status).

**Files to create/modify:**
- `services/external-events/src/external_events/infrastructure/persistence/`

**Implementation notes:**
- operational schema; EndpointConfig holds provider, auth_strategy, secret_ref, status.

**References:** ID-058, MAS §9.2.

**Acceptance criteria:**
- [ ] Alembic baseline + model present
- [ ] ≥90% coverage

---

## MESSAGING-INFRA Epic 🤖

### SFP-97 [MSG-INFRA] 🤖 — Pulumi: SNS topics
**Labels:** platform, ai-agent, msg-infra | **Deps:** SFP-66 | **Context out:** `topic_arns`

**Context:**
Events fan out via SNS (ID-009, MAS §4.4).

**Requirements:**
- One SNS topic per event/domain; export ARNs.

**Files to create/modify:**
- `infrastructure/services/messaging/topics.py`

**Implementation notes:**
- One topic per event type; consumers subscribe via SQS. Standard topics (ID-010).

**References:** ID-009.

**Acceptance criteria:**
- [ ] Topics created; ARNs exported
- [ ] `pulumi up` succeeds

---

### SFP-98 [MSG-INFRA] 🤖 — Pulumi: SQS queues
**Labels:** platform, ai-agent, msg-infra | **Deps:** SFP-66 | **Context out:** `queue_urls`

**Context:**
Each consumer buffers work in its own SQS queue (ID-009).

**Requirements:**
- One SQS queue per consumer; export URLs.

**Files to create/modify:**
- `infrastructure/services/messaging/queues.py`

**Implementation notes:**
- Standard queues; one per consumer; visibility timeout tuned to processing time.

**References:** ID-009.

**Acceptance criteria:**
- [ ] Queues created; URLs exported
- [ ] `pulumi up` succeeds

---

### SFP-99 [MSG-INFRA] 🤖 — Pulumi: DLQs + redrive policies
**Labels:** platform, ai-agent, msg-infra | **Deps:** SFP-98 | **Context out:** `dlq_urls`

**Context:**
Poison messages move to a DLQ (ID-012).

**Requirements:**
- One DLQ per queue with a redrive policy (`maxReceiveCount`).

**Files to create/modify:**
- `infrastructure/services/messaging/dlq.py`

**Implementation notes:**
- maxReceiveCount ~3-5; alert on DLQ depth.

**References:** ID-012.

**Acceptance criteria:**
- [ ] DLQs created and attached via redrive policy
- [ ] `pulumi up` succeeds

---

### SFP-100 [MSG-INFRA] 🤖 — Pulumi: SNS→SQS subscriptions
**Labels:** platform, ai-agent, msg-infra | **Deps:** SFP-97, SFP-98

**Context:**
Topics fan out to consumer queues (MAS §4.4).

**Requirements:**
- Wire SNS topics to the appropriate consumer SQS queues.

**Files to create/modify:**
- `infrastructure/services/messaging/subscriptions.py`

**Implementation notes:**
- SNS→SQS fan-out; raw message delivery off (we manage the envelope, ID-031).

**References:** ID-009.

**Acceptance criteria:**
- [ ] Subscriptions created; a published message reaches the subscribed queue
- [ ] `pulumi up` succeeds

---

### SFP-101 [MSG-INFRA] 🤖 — sfp-messaging SNS/SQS transport
**Labels:** platform, ai-agent, msg-infra | **Deps:** SFP-29, SFP-97, SFP-98 *(B→A)*

**Context:**
The prod transport implements the Manual-Core Message Bus abstraction (SFP-29) over SNS/SQS.

**Requirements:**
- Implement `MessageBus` using boto3 SNS (publish) + SQS (poll/ack); hide all transport details from business code.

**Files to create/modify:**
- `packages/sfp-messaging/src/sfp_messaging/transport/sns_sqs.py`

**Implementation notes:**
- Implements the SFP-25 abstraction with boto3; inject/extract envelope attrs (SFP-102). Ack only after durable processing.

**References:** ID-009, ID-052.

**Acceptance criteria:**
- [ ] Publish delivers to subscribed queues; ack only after durable processing
- [ ] ≥90% coverage

---

### SFP-102 [MSG-INFRA] 🤖 — Envelope propagation across SNS/SQS
**Labels:** platform, ai-agent, msg-infra | **Deps:** SFP-28, SFP-101 *(B→A)*

**Context:**
Correlation/causation/idempotency identifiers must survive the bus (ID-031).

**Requirements:**
- Inject/extract the envelope (`idempotency_key`, `correlation_id`, `causation_id`, `message_id`) into/out of SQS message attributes on publish/consume.

**Files to create/modify:**
- `packages/sfp-messaging/src/sfp_messaging/transport/envelope_attrs.py`

**Implementation notes:**
- Store correlation_id/causation_id/idempotency_key/message_id as SQS message attributes; round-trip lossless (ID-031).

**References:** ID-031, ID-013.

**Acceptance criteria:**
- [ ] Identifiers round-trip across SNS→SQS
- [ ] ≥90% coverage

---

## EXTERNAL-EVENTS Epic 🤖

### SFP-103 [EXT-EVT] 🤖 — Webhook endpoint `/webhooks/{endpoint_id}`
**Labels:** platform, ai-agent, external-events | **Deps:** SFP-96, SFP-101

**Context:**
Single ingress for all external providers (MAS §9.2, ID-028).

**Requirements:**
- FastAPI route `POST /webhooks/{endpoint_id}` that resolves config, authenticates, wraps the payload, and publishes `ExternalEventReceived`.
- Validate only ingress-level concerns; never interpret the provider payload.

**Files to create/modify:**
- `services/external-events/src/external_events/interfaces/webhook.py`

**Implementation notes:**
- FastAPI; resolve endpoint (SFP-104) → authenticate (SFP-105/106) → wrap → publish (SFP-107). Reject unknown endpoint / auth failure.

**References:** MAS §9.2, ID-028.

**Acceptance criteria:**
- [ ] Authenticated request → `ExternalEventReceived` published
- [ ] Unauthenticated/unknown endpoint → rejected
- [ ] ≥90% coverage

---

### SFP-104 [EXT-EVT] 🤖 — Endpoint config resolver
**Labels:** platform, ai-agent, external-events | **Deps:** SFP-103

**Context:**
The endpoint_id resolves provider, auth strategy, and secret reference (MAS §9.2).

**Requirements:**
- Load `EndpointConfig` by `endpoint_id` (SFP-96); return provider/auth/secret/status.

**Files to create/modify:**
- `services/external-events/src/external_events/application/endpoint_resolver.py`

**Implementation notes:**
- Read EndpointConfig from SFP-96; return provider/auth/secret/status; cache locally.

**References:** MAS §9.2.

**Acceptance criteria:**
- [ ] Resolves a configured endpoint; unknown endpoint → not found
- [ ] ≥90% coverage

---

### SFP-105 [EXT-EVT] 🤖 — Authentication strategy factory
**Labels:** platform, ai-agent, external-events | **Deps:** SFP-104

**Context:**
Different providers authenticate differently (ID-029).

**Requirements:**
- Select an authentication strategy from the endpoint config; strategies receive injected secrets (never load secrets themselves).

**Files to create/modify:**
- `services/external-events/src/external_events/application/auth_factory.py`

**Implementation notes:**
- Select strategy from endpoint provider; inject the secret (strategies never load secrets, ID-029).

**References:** ID-029.

**Acceptance criteria:**
- [ ] Correct strategy selected per provider
- [ ] ≥90% coverage

---

### SFP-106 [EXT-EVT] 🤖 — Authentication strategies (HMAC, signatures)
**Labels:** platform, ai-agent, external-events | **Deps:** SFP-105

**Context:**
Concrete per-provider authentication (ID-029).

**Requirements:**
- Strategies for the v0 providers (e.g. Slack signature, GitHub HMAC).

**Files to create/modify:**
- `services/external-events/src/external_events/application/strategies/`

**Implementation notes:**
- Constant-time compare for HMAC/signatures; reject on any mismatch.

**References:** ID-029, MAS §9.2.

**Acceptance criteria:**
- [ ] Each strategy validates a well-formed request and rejects a tampered one
- [ ] ≥90% coverage

---

### SFP-107 [EXT-EVT] 🤖 — ExternalEventReceived publisher
**Labels:** platform, ai-agent, external-events | **Deps:** SFP-103, SFP-101

**Context:**
Authenticated payloads are wrapped and published without interpretation (MAS §9.2).

**Requirements:**
- Build `ExternalEventReceived` (SFP-23) with an opaque payload and publish it.

**Files to create/modify:**
- `services/external-events/src/external_events/application/publisher.py`

**Implementation notes:**
- Build ExternalEventReceived (SFP-23) with opaque payload + generated external_event_id; publish via MessageBus.

**References:** MAS §9.2, ID-031.

**Acceptance criteria:**
- [ ] Published event carries opaque payload + identifiers
- [ ] ≥90% coverage

---

## IDENTITY Epic 🤖

### SFP-108 [IDENT] 🤖 — User aggregate + lifecycle
**Labels:** platform, ai-agent, identity | **Deps:** SFP-94, SFP-101

**Context:**
Identity owns the canonical User (MAS §9.3).

**Requirements:**
- `User` domain entity + lifecycle (create/update); immutable `user_id`.

**Files to create/modify:**
- `services/identity/src/identity/domain/user.py`
- `services/identity/src/identity/application/`

**Implementation notes:**
- user_id immutable; lifecycle managed here only; other services reference by user_id.

**References:** MAS §9.3.

**Acceptance criteria:**
- [ ] User lifecycle operations persist correctly
- [ ] ≥90% coverage

---

### SFP-109 [IDENT] 🤖 — UserExternalIdentity + mappings
**Labels:** platform, ai-agent, identity | **Deps:** SFP-108

**Context:**
Users are linked to external identities (Slack, GitHub) (MAS §9.3).

**Requirements:**
- `UserExternalIdentity` mapping; one user may have multiple external identities.

**Files to create/modify:**
- `services/identity/src/identity/domain/external_identity.py`

**Implementation notes:**
- Unique on (provider, provider_user_id); one user → many external identities.

**References:** MAS §9.3.

**Acceptance criteria:**
- [ ] Mappings created/queried; one-to-many supported
- [ ] ≥90% coverage

---

### SFP-110 [IDENT] 🤖 — Identity resolution (external↔platform)
**Labels:** platform, ai-agent, identity | **Deps:** SFP-108, SFP-109

**Context:**
Services need to resolve external identities to platform users and vice versa (MAS §9.3).

**Requirements:**
- Resolve a `user_id` from an external identity, and the external identity for a user/provider.

**Files to create/modify:**
- `services/identity/src/identity/application/resolver.py`

**Implementation notes:**
- external→platform via the mapping; platform→external by provider. Explicit failure if unresolved.

**References:** MAS §9.3.

**Acceptance criteria:**
- [ ] External→platform and platform→external resolution works
- [ ] Unknown identity → explicit failure
- [ ] ≥90% coverage

---

### SFP-111 [IDENT] 🤖 — Identity read-only query API
**Labels:** platform, ai-agent, identity | **Deps:** SFP-110

**Context:**
Other services retrieve identity via read-only queries (MAS §5.12).

**Requirements:**
- `ResolveUser` and `ResolveExternalIdentity` query endpoints (read-only, no side effects).

**Files to create/modify:**
- `services/identity/src/identity/interfaces/queries.py`

**Implementation notes:**
- Read-only (MAS §5.12); no side effects; used by Communication/Orchestrator.

**References:** MAS §5.12.

**Acceptance criteria:**
- [ ] Query endpoints return identity without side effects
- [ ] ≥90% coverage

---

## COMMUNICATION Epic 🤖

### SFP-112 [COMM] 🤖 — UserInteraction lifecycle (create/expire/complete)
**Labels:** platform, ai-agent, communication | **Deps:** SFP-95, SFP-101

**Context:**
Communication owns the UserInteraction lifecycle (MAS §9.4).

**Requirements:**
- Create/complete/expire a UserInteraction; one Slack thread maps 1:1 to one interaction (v0).
- Handler for `RequestUserInput`/`NotifyUser` commands.

**Files to create/modify:**
- `services/communication/src/communication/application/interaction_service.py`

**Implementation notes:**
- Handler for RequestUserInput/NotifyUser; one Slack thread = one interaction in v0.

**References:** MAS §9.4.

**Acceptance criteria:**
- [ ] Lifecycle transitions persist and emit `UserInteractionUpdated`
- [ ] ≥90% coverage

---

### SFP-113 [COMM] 🤖 — UserInteraction summary + expiration timer
**Labels:** platform, ai-agent, communication | **Deps:** SFP-112

**Context:**
Every interaction carries a durable summary and expires after 8h of inactivity (MAS §9.4).

**Requirements:**
- Maintain/update the summary; reset the expiration timer on each message.

**Files to create/modify:**
- `services/communication/src/communication/application/summary.py`

**Implementation notes:**
- Communication Agent (SFP-117) generates the summary; timer resets on each message; expire at 8h inactivity.

**References:** MAS §9.4, AP-009.

**Acceptance criteria:**
- [ ] Summary updated; expiration resets on activity
- [ ] ≥90% coverage

---

### SFP-114 [COMM] 🤖 — Closed-interaction handling (no reopen; context ref)
**Labels:** platform, ai-agent, communication | **Deps:** SFP-112

**Context:**
Completed/expired interactions are immutable; a reply starts a new interaction (MAS §9.4).

**Requirements:**
- On a message to a closed interaction: keep it closed, ask the user to start a new thread, carry the previous summary as context.

**Files to create/modify:**
- `services/communication/src/communication/application/closed_handling.py`

**Implementation notes:**
- Reply to a closed interaction → keep closed, ask for a new thread, carry prior summary as context.

**References:** MAS §9.4.

**Acceptance criteria:**
- [ ] Closed interaction is never reopened; new interaction carries prior context
- [ ] ≥90% coverage

---

### SFP-115 [COMM] 🤖 — Slack provider: inbound event handling
**Labels:** platform, ai-agent, communication | **Deps:** SFP-107, SFP-112

**Context:**
Slack events arrive as `ExternalEventReceived` and are interpreted by Communication (ID-027, MAS §9.4).

**Requirements:**
- Consume `ExternalEventReceived` (provider=Slack); interpret via the local Slack schema; update the UserInteraction; publish `UserInputReceived`/`UserQueryReceived`.

**Files to create/modify:**
- `services/communication/src/communication/interfaces/slack_inbound.py`

**Implementation notes:**
- Consume ExternalEventReceived(provider=Slack) → local Slack schema → update interaction → emit UserInputReceived/UserQueryReceived.

**References:** ID-027, MAS §9.4.

**Acceptance criteria:**
- [ ] Inbound Slack event updates the interaction and emits the right event
- [ ] ≥90% coverage

---

### SFP-116 [COMM] 🤖 — Slack provider: outbound messages
**Labels:** platform, ai-agent, communication | **Deps:** SFP-112

**Context:**
Outbound messages go to the user's Slack thread (ID-027).

**Requirements:**
- Send messages/replies to Slack via the provider abstraction (httpx).

**Files to create/modify:**
- `services/communication/src/communication/interfaces/slack_outbound.py`

**Implementation notes:**
- Post via the Slack API (httpx); resolve channel/thread from provider_reference.

**References:** ID-027, ID-051.

**Acceptance criteria:**
- [ ] Outbound message posted to the correct thread
- [ ] ≥90% coverage

---

### SFP-117 [COMM] 🤖 — Communication Agent (summarization, context)
**Labels:** platform, ai-agent, communication | **Deps:** SFP-112, SFP-36 *(B→A)*

**Context:**
The Communication Agent summarizes and reconstructs context (MAS §9.4). It uses the Manual-Core Agent Runtime (SFP-36).

**Requirements:**
- Summarize interactions; reconstruct context from summary + inbound message + provider context.
- Execute Communication policies; never define platform behaviour.

**Files to create/modify:**
- `services/communication/src/communication/application/communication_agent.py`

**Implementation notes:**
- Uses the Agent Runtime (SFP-36) to summarize + reconstruct context; executes Communication policies only (MAS §9.4).

**References:** MAS §9.4.

**Acceptance criteria:**
- [ ] Produces a structured summary from an interaction
- [ ] ≥90% coverage

---

### SFP-118 [COMM] 🤖 — CONFIRM flow (summary→CONFIRM→UserDecision)
**Labels:** platform, ai-agent, communication | **Deps:** SFP-112, SFP-117

**Context:**
Every interaction ends with a generated summary and explicit CONFIRM before persistence (ID-069).

**Requirements:**
- Generate summary → request `CONFIRM` → on confirm, persist a `UserDecision`; on correction, regenerate and re-request.

**Files to create/modify:**
- `services/communication/src/communication/application/confirm_flow.py`

**Implementation notes:**
- Generate summary → require literal CONFIRM → persist UserDecision; corrections regenerate (ID-069).

**References:** ID-069, MAS §9.4.

**Acceptance criteria:**
- [ ] Only CONFIRMED summaries become UserDecisions
- [ ] Corrections trigger regeneration
- [ ] ≥90% coverage

---

### SFP-119 [COMM] 🤖 — Notifications + input requests (NotifyUser/RequestUserInput)
**Labels:** platform, ai-agent, communication | **Deps:** SFP-116

**Context:**
The Orchestrator requests notifications and user input through Communication (MAS §9.4).

**Requirements:**
- Handle `NotifyUser` and `RequestUserInput`; route via identity + Slack outbound.

**Files to create/modify:**
- `services/communication/src/communication/application/notifications.py`

**Implementation notes:**
- Route via Identity (resolve user→Slack) then Slack outbound; emit immediately (MAS §11.8).

**References:** MAS §9.4, MAS §11.8.

**Acceptance criteria:**
- [ ] Notifications/input requests delivered to the right user
- [ ] ≥90% coverage

---

## ORCHESTRATOR Epic 🤖

### SFP-120 [ORCH] 🤖 — Workflow engine + state machine
**Labels:** platform, ai-agent, orchestrator | **Deps:** SFP-82, SFP-101

**Context:**
The Orchestrator owns deterministic workflow progression (MAS §8).

**Requirements:**
- A state machine over the Ticket workflow states (§8.4); explicit transitions only.

**Files to create/modify:**
- `services/orchestrator/src/orchestrator/domain/workflow/{states.py,state_machine.py}`

**Implementation notes:**
- State machine enforces §8.4 transitions; no implicit moves; every transition records a WorkflowDecision (SFP-131).

**References:** MAS §8.

**Acceptance criteria:**
- [ ] State machine enforces valid transitions; invalid → error
- [ ] ≥90% coverage

---

### SFP-121 [ORCH] 🤖 — Transitions: ticket→PR-spec stage
**Labels:** platform, ai-agent, orchestrator | **Deps:** SFP-120

**Context:** Ticket intake through planning.

**Requirements:** `READY_FOR_PR_SPECIFICATION → READY_FOR_CODING` after a successful plan.

**Files to create/modify:** `.../domain/workflow/transitions.py`

**Implementation notes:**
- READY_FOR_PR_SPECIFICATION → READY_FOR_CODING after a successful plan (SFP-133).

**References:** MAS §8.4.

**Acceptance criteria:**
- [ ] Transition correct and guarded
- [ ] ≥90% coverage

---

### SFP-122 [ORCH] 🤖 — Transitions: coding/review stages
**Labels:** platform, ai-agent, orchestrator | **Deps:** SFP-120

**Context:**
Transitions through coding and review, including the rework loop.

**Requirements:** `READY_FOR_CODING → CODING_IN_PROGRESS → REVIEW_IN_PROGRESS` (and rework loops).

**Files to create/modify:** `.../domain/workflow/transitions.py`

**Implementation notes:**
- READY_FOR_CODING→CODING_IN_PROGRESS→REVIEW_IN_PROGRESS; CHANGES_REQUESTED loops back to CODING (rework, not failure — ID-068).

**References:** MAS §8.4, ID-068.

**Acceptance criteria:**
- [ ] Coding/review transitions and rework correct
- [ ] ≥90% coverage

---

### SFP-123 [ORCH] 🤖 — Transitions: merge/deploy stages
**Labels:** platform, ai-agent, orchestrator | **Deps:** SFP-120

**Context:**
Final stages: merge and deployment observation.

**Requirements:** `READY_FOR_MERGE → MERGING → DEPLOYING → COMPLETED`.

**Files to create/modify:** `.../domain/workflow/transitions.py`

**Implementation notes:**
- READY_FOR_MERGE→MERGING→DEPLOYING→COMPLETED; merge only after human approval where required (ID-024).

**References:** MAS §8.4, ID-024.

**Acceptance criteria:**
- [ ] Merge/deploy transitions correct
- [ ] ≥90% coverage

---

### SFP-124 [ORCH] 🤖 — Transitions: WAITING_FOR_USER + failure/terminal
**Labels:** platform, ai-agent, orchestrator | **Deps:** SFP-120

**Context:**
User-waiting and terminal transitions.

**Requirements:** `↔ WAITING_FOR_USER`, `→ FAILED`, terminal `COMPLETED`.

**Files to create/modify:** `.../domain/workflow/transitions.py`

**Implementation notes:**
- ↔ WAITING_FOR_USER on RequestUserInput; → FAILED on failure; COMPLETED terminal.

**References:** MAS §8.4.

**Acceptance criteria:**
- [ ] Waiting/failure/terminal transitions correct
- [ ] ≥90% coverage

---

### SFP-125 [ORCH] 🤖 — Policy engine (deterministic evaluation)
**Labels:** platform, ai-agent, orchestrator | **Deps:** SFP-120

**Context:**
Transitions are governed by deterministic, pure policies (MAS §8.14).

**Requirements:**
- Evaluator: `(current state, business facts) → decision (transition, commands)`; side-effect free; same inputs → same output.

**Files to create/modify:** `.../domain/workflow/policy_engine.py`

**Implementation notes:**
- Pure functions: (state, facts)→decision; same inputs→same output; never execute work (MAS §8.14).

**References:** MAS §8.14.

**Acceptance criteria:**
- [ ] Deterministic; pure
- [ ] ≥90% coverage

---

### SFP-126 [ORCH] 🤖 — Policies: coding-start / review-success / merge-ready
**Labels:** platform, ai-agent, orchestrator | **Deps:** SFP-125

**Context:**
The go/no-go policies for the main pipeline stages.

**Requirements:** `MayCodingStart`, `HasReviewSucceeded`, `CanMerge` policies.

**Files to create/modify:** `.../domain/workflow/policies/`

**Implementation notes:**
- MayCodingStart (locked spec present), HasReviewSucceeded (APPROVED + gates), CanMerge (approval per profile).

**References:** MAS §8.14, ID-023.

**Acceptance criteria:**
- [ ] Each policy returns the correct decision for representative inputs
- [ ] ≥90% coverage

---

### SFP-127 [ORCH] 🤖 — Policies: user-approval / deploy-begin / fail
**Labels:** platform, ai-agent, orchestrator | **Deps:** SFP-125

**Context:**
Policies gating approval, deployment, and failure.

**Requirements:** `IsUserApprovalRequired` (LEVEL_2+), `ShouldDeployBegin`, `ShouldFail` policies.

**Files to create/modify:** `.../domain/workflow/policies/`

**Implementation notes:**
- IsUserApprovalRequired (LEVEL_2+, ID-024/067), ShouldDeployBegin, ShouldFail (only genuine failure causes, ID-068).

**References:** ID-024, ID-067.

**Acceptance criteria:**
- [ ] Each policy returns the correct decision
- [ ] ≥90% coverage

---

### SFP-128 [ORCH] 🤖 — Scheduler: admission + concurrency
**Labels:** platform, ai-agent, orchestrator | **Deps:** SFP-125, SFP-76

**Context:**
The Scheduler owns execution admission against Batch capacity (MAS §11.8, ID-061).

**Requirements:**
- Admit execution-bound commands up to capacity; emit communication commands immediately.

**Files to create/modify:** `.../domain/scheduler.py`

**Implementation notes:**
- Admit execution commands up to Batch capacity; emit communication commands immediately (MAS §11.8).

**References:** ID-061, MAS §11.8.

**Acceptance criteria:**
- [ ] Admits up to capacity; communication commands immediate
- [ ] ≥90% coverage

---

### SFP-129 [ORCH] 🤖 — Scheduler: max-vCpus ceiling + dispatch
**Labels:** platform, ai-agent, orchestrator | **Deps:** SFP-128

**Context:**
The Scheduler enforces the concurrency ceiling and dispatches.

**Requirements:** Enforce the Batch `max-vCpus` ceiling (token/compute cap); dispatch execution commands.

**Files to create/modify:** `.../domain/scheduler.py`

**Implementation notes:**
- Cap concurrency via Batch max-vCpus (SFP-76); never change workflow state — only when execution-bound commands emit (ID-061).

**References:** ID-061.

**Acceptance criteria:**
- [ ] Ceiling enforced; dispatch correct
- [ ] ≥90% coverage

---

### SFP-130 [ORCH] 🤖 — Aggregate manager (consistency)
**Labels:** platform, ai-agent, orchestrator | **Deps:** SFP-120

**Context:**
Aggregate-level consistency (MAS §9.5).

**Requirements:**
- Load/modify/save aggregates through aggregate rules; enforce transaction boundaries.

**Files to create/modify:** `.../domain/aggregate_manager.py`

**Implementation notes:**
- All aggregate mutations go through aggregate-level rules + a transaction boundary (MAS §9.5).

**References:** MAS §9.5.

**Acceptance criteria:**
- [ ] Aggregate modifications enforced via rules
- [ ] ≥90% coverage

---

### SFP-131 [ORCH] 🤖 — Decision recorder (WorkflowDecision persistence)
**Labels:** platform, ai-agent, orchestrator | **Deps:** SFP-130

**Context:**
Every workflow-affecting transition records an immutable WorkflowDecision (MAS §8.5, ID-072).

**Requirements:**
- Persist a `WorkflowDecision` linking facts → policy → commands → state; no transition without one.

**Files to create/modify:** `.../application/decision_recorder.py`

**Implementation notes:**
- Persist a WorkflowDecision for every transition; no command emitted without one (ID-072, MAS §8.5).

**References:** MAS §8.5, ID-072.

**Acceptance criteria:**
- [ ] Every emitted command is traceable to a WorkflowDecision
- [ ] ≥90% coverage

---

### SFP-132 [ORCH] 🤖 — Hosting: Readiness Gate wiring
**Labels:** platform, ai-agent, orchestrator | **Deps:** SFP-51, SFP-120 *(B→A)*

**Context:**
The Orchestrator hosts the Manual-Core Readiness Gate (SFP-51) (ID-072).

**Requirements:**
- Invoke the Readiness Gate before planning; act on its verdicts.

**Files to create/modify:** `.../application/readiness_host.py`

**Implementation notes:**
- Invoke SFP-51 before planning; route by verdict (READY/NEEDS_CLARIFICATION/MANUAL_REQUIRED).

**References:** ID-064, ID-065.

**Acceptance criteria:**
- [ ] Readiness verdict drives whether a ticket is planned
- [ ] ≥90% coverage

---

### SFP-133 [ORCH] 🤖 — Hosting: Planner wiring
**Labels:** platform, ai-agent, orchestrator | **Deps:** SFP-53, SFP-120 *(B→A)*

**Context:**
Wire the Manual-Core Planner into the Orchestrator (ID-072).

**Requirements:** Invoke the Planner (SFP-53) to produce PR-specs from a ready ticket.

**Files to create/modify:** `.../application/planner_host.py`

**Implementation notes:**
- Invoke SFP-53 on a READY ticket; persist the resulting PRSpecifications.

**References:** ID-021, ID-072.

**Acceptance criteria:**
- [ ] Planning produces persisted PR-specifications
- [ ] ≥90% coverage

---

### SFP-134 [ORCH] 🤖 — Hosting: context resolver wiring
**Labels:** platform, ai-agent, orchestrator | **Deps:** SFP-49, SFP-120 *(B→A)*

**Context:**
Wire the context resolver so dependency inputs are resolved before planning.

**Requirements:** Use the context resolver (SFP-49) to resolve dependency inputs before planning/coding.

**Files to create/modify:** `.../application/context_host.py`

**Implementation notes:**
- Invoke SFP-49; inject resolved inputs into PR-specs; surface missing inputs.

**References:** ID-071, ID-072.

**Acceptance criteria:**
- [ ] Resolved inputs are injected into PR-specs
- [ ] ≥90% coverage

---

### SFP-135 [ORCH] 🤖 — Command emission: ExecuteCodingJob
**Labels:** platform, ai-agent, orchestrator | **Deps:** SFP-120, SFP-101

**Context:**
Emit ExecuteCodingJob to start coding.

**Requirements:** Emit `ExecuteCodingJob` (SFP-21) on `READY_FOR_CODING`.

**Files to create/modify:** `.../application/command_emitters.py`

**Implementation notes:**
- Emit via MessageBus with envelope + idempotency_key; record in the WorkflowDecision.

**References:** MAS §5.3, ID-031, ID-072.

**Acceptance criteria:**
- [ ] Command emitted with envelope + idempotency_key
- [ ] ≥90% coverage

---

### SFP-136 [ORCH] 🤖 — Command emission: ReviewPullRequest
**Labels:** platform, ai-agent, orchestrator | **Deps:** SFP-120

**Context:**
Emit ReviewPullRequest.

**Requirements:** Emit `ReviewPullRequest` on the review stage.

**Files to create/modify:** `.../application/command_emitters.py`

**Implementation notes:**
- Via MessageBus + envelope; idempotent; recorded in the WorkflowDecision; emitted on the review stage.

**References:** MAS §5.3, ID-031.

**Acceptance criteria:**
- [ ] Command emitted correctly
- [ ] ≥90% coverage

---

### SFP-137 [ORCH] 🤖 — Command emission: SynchronizePullRequest
**Labels:** platform, ai-agent, orchestrator | **Deps:** SFP-120

**Context:**
Emit SynchronizePullRequest.

**Requirements:** Emit `SynchronizePullRequest` to keep the PR branch current.

**Files to create/modify:** `.../application/command_emitters.py`

**Implementation notes:**
- Via MessageBus + envelope; idempotent; emitted when the base moves.

**References:** MAS §5.3, ID-031.

**Acceptance criteria:**
- [ ] Command emitted correctly
- [ ] ≥90% coverage

---

### SFP-138 [ORCH] 🤖 — Command emission: RequestMerge
**Labels:** platform, ai-agent, orchestrator | **Deps:** SFP-120

**Context:**
Emit RequestMerge.

**Requirements:** Emit `RequestMerge` once merge is approved.

**Files to create/modify:** `.../application/command_emitters.py`

**Implementation notes:**
- Via MessageBus + envelope; emitted only after approval (ID-024).

**References:** MAS §5.3, ID-031, ID-024.

**Acceptance criteria:**
- [ ] Command emitted only after approval
- [ ] ≥90% coverage

---

### SFP-139 [ORCH] 🤖 — Command emission: RequestUserInput + NotifyUser
**Labels:** platform, ai-agent, orchestrator | **Deps:** SFP-120

**Context:**
Emit RequestUserInput + NotifyUser.

**Requirements:** Emit `RequestUserInput`/`NotifyUser` to Communication.

**Files to create/modify:** `.../application/command_emitters.py`

**Implementation notes:**
- To Communication; emitted immediately (MAS §11.8).

**References:** MAS §5.3, ID-031.

**Acceptance criteria:**
- [ ] Commands emitted to Communication
- [ ] ≥90% coverage

---

### SFP-140 [ORCH] 🤖 — Command emission: CancelCodingJob + CancelReviewJob
**Labels:** platform, ai-agent, orchestrator | **Deps:** SFP-120

**Context:**
Emit CancelCodingJob + CancelReviewJob.

**Requirements:** Emit cancellation commands on cancel/failure.

**Files to create/modify:** `.../application/command_emitters.py`

**Implementation notes:**
- On cancel/failure; via MessageBus + envelope.

**References:** MAS §5.3, ID-031.

**Acceptance criteria:**
- [ ] Cancellation commands emitted correctly
- [ ] ≥90% coverage

---

### SFP-141 [ORCH] 🤖 — Query API: RetrieveWorkflowContext
**Labels:** platform, ai-agent, orchestrator | **Deps:** SFP-120

**Context:** Read-only query (MAS §5.12).

**Requirements:** `RetrieveWorkflowContext` endpoint (read-only, no side effects).

**Files to create/modify:** `.../interfaces/queries.py`

**Implementation notes:**
- Read-only (MAS §5.12); return workflow context for agents/communication.

**References:** MAS §5.12.

**Acceptance criteria:**
- [ ] Returns workflow context; no side effects
- [ ] ≥90% coverage

---

### SFP-142 [ORCH] 🤖 — Query API: RetrieveTicketSummary + RetrieveProject
**Labels:** platform, ai-agent, orchestrator | **Deps:** SFP-120

**Context:**
Read-only summary queries.

**Requirements:** `RetrieveTicketSummary` and `RetrieveProject` read-only queries.

**Files to create/modify:** `.../interfaces/queries.py`

**Implementation notes:**
- RetrieveTicketSummary/RetrieveProject; no side effects.

**References:** MAS §5.12.

**Acceptance criteria:**
- [ ] Queries return summaries; no side effects
- [ ] ≥90% coverage

---

## WORKSPACE-WORKER Epic 🤖

### SFP-143 [WSW] 🤖 — Execution coordinator service skeleton
**Labels:** platform, ai-agent, workspace-worker | **Deps:** SFP-101, SFP-36, SFP-55, SFP-56 *(B→A)*

**Context:**
The Workspace Worker hosts the Manual-Core Agent Runtime + Coder/Reviewer; it is execution-only (MAS §9.6).

**Requirements:**
- Service skeleton: command consumers wired to the Message Bus; delegates work to the Agent Runtime.

**Files to create/modify:**
- `services/workspace-worker/src/workspace_worker/entrypoints/`

**Implementation notes:**
- Composition root (ID-052) wires Agent Runtime + consumers; liveness/readiness endpoints (ID-043); MAS §11.2 startup lifecycle.

**References:** MAS §9.6.

**Acceptance criteria:**
- [ ] Service boots, consumes messages, delegates to the runtime
- [ ] ≥90% coverage

---

### SFP-144 [WSW] 🤖 — Consumer: ExecuteCodingJob
**Labels:** platform, ai-agent, workspace-worker | **Deps:** SFP-143, SFP-101, SFP-76

**Context:**
Handle ExecuteCodingJob by running the Coder.

**Requirements:** Handle `ExecuteCodingJob`: run the Coder (SFP-55) as a Batch task; report outcome.

**Files to create/modify:** `.../application/execute_coding_job.py`

**Implementation notes:**
- Idempotent via ledger (ID-011); run the Coder (SFP-55) as a Batch task (SFP-149); report CodingJobUpdated.

**References:** MAS §9.6, ID-011.

**Acceptance criteria:**
- [ ] Command handled idempotently; outcome reported
- [ ] ≥90% coverage

---

### SFP-145 [WSW] 🤖 — Consumer: ReviewPullRequest
**Labels:** platform, ai-agent, workspace-worker | **Deps:** SFP-143

**Context:**
Handle ReviewPullRequest by running the Reviewer.

**Requirements:** Handle `ReviewPullRequest`: run the Reviewer (SFP-56); submit review.

**Files to create/modify:** `.../application/review_pull_request.py`

**Implementation notes:**
- Run the Reviewer (SFP-56); submit the review; report ReviewUpdated.

**References:** MAS §9.6, ID-023.

**Acceptance criteria:**
- [ ] Command handled; review submitted
- [ ] ≥90% coverage

---

### SFP-146 [WSW] 🤖 — Consumer: SynchronizePullRequest
**Labels:** platform, ai-agent, workspace-worker | **Deps:** SFP-143

**Context:**
Handle SynchronizePullRequest.

**Requirements:** Handle `SynchronizePullRequest`: sync the PR branch (SFP-44).

**Files to create/modify:** `.../application/synchronize.py`

**Implementation notes:**
- Sync via the Git Provider Adapter (SFP-44); report the outcome.

**References:** MAS §9.6, ID-035.

**Acceptance criteria:**
- [ ] Branch synchronized
- [ ] ≥90% coverage

---

### SFP-147 [WSW] 🤖 — Consumer: RequestMerge
**Labels:** platform, ai-agent, workspace-worker | **Deps:** SFP-143

**Context:**
Handle RequestMerge.

**Requirements:** Handle `RequestMerge`: execute the merge (SFP-153).

**Files to create/modify:** `.../application/request_merge.py`

**Implementation notes:**
- Execute the merge (SFP-153); report MergeUpdated.

**References:** MAS §9.6.

**Acceptance criteria:**
- [ ] Merge executed only on explicit request
- [ ] ≥90% coverage

---

### SFP-148 [WSW] 🤖 — Consumer: CancelCodingJob + CancelReviewJob
**Labels:** platform, ai-agent, workspace-worker | **Deps:** SFP-143

**Context:**
Handle cancellation commands.

**Requirements:** Handle cancellation commands; stop/clean up in-flight work.

**Files to create/modify:** `.../application/cancellations.py`

**Implementation notes:**
- Stop/clean in-flight work; ephemeral state only (ID-033).

**References:** MAS §9.6.

**Acceptance criteria:**
- [ ] Cancellation handled; in-flight task cleaned up
- [ ] ≥90% coverage

---

### SFP-149 [WSW] 🤖 — SQS→Batch bridge + task-per-job dispatch
**Labels:** platform, ai-agent, workspace-worker | **Deps:** SFP-144, SFP-76

**Context:**
Each command runs as an ephemeral Batch task (ID-060/061).

**Requirements:**
- A bridge that submits one Batch task per dequeued command, preserving the Message Bus contract.

**Files to create/modify:** `.../application/batch_bridge.py`

**Implementation notes:**
- Submit one Batch task per dequeued command; preserve the Message Bus contract; scale-to-zero (ID-060/061).

**References:** ID-060, ID-061.

**Acceptance criteria:**
- [ ] One Batch task per command; scale-to-zero when idle
- [ ] ≥90% coverage

---

### SFP-150 [WSW] 🤖 — Batch task definition (container, sandbox, egress policy)
**Labels:** platform, ai-agent, workspace-worker | **Deps:** SFP-76, SFP-48 *(B→A)*

**Context:**
The Batch task definition enforces the sandbox profile.

**Requirements:** Batch task definition with the sandbox profile (restricted FS, no default egress, dropped caps, non-root, limits, teardown).

**Files to create/modify:** `.../infrastructure/batch_task.py`

**Implementation notes:**
- Restricted FS, no default egress (Git Provider Adapter host only), dropped caps, non-root, limits, teardown (ID-060/SFP-48).

**References:** ID-060.

**Acceptance criteria:**
- [ ] Task runs with the sandbox profile; egress allow-listed
- [ ] ≥90% coverage

---

### SFP-151 [WSW] 🤖 — Execution reporting: CodingJobUpdated
**Labels:** platform, ai-agent, workspace-worker | **Deps:** SFP-143

**Context:**
Report the coding execution outcome.

**Requirements:** Publish `CodingJobUpdated` with execution outcome.

**Files to create/modify:** `.../application/reporting.py`

**Implementation notes:**
- Publish CodingJobUpdated with status/reason; status=FAILED+INSUFFICIENT_SPECIFICATION when the spec is insufficient (ID-032).

**References:** MAS §9.6, ID-032.

**Acceptance criteria:**
- [ ] Event published with correct status/reason
- [ ] ≥90% coverage

---

### SFP-152 [WSW] 🤖 — Execution reporting: ReviewUpdated + MergeUpdated
**Labels:** platform, ai-agent, workspace-worker | **Deps:** SFP-143

**Context:**
Report review and merge outcomes.

**Requirements:** Publish `ReviewUpdated` and `MergeUpdated`.

**Files to create/modify:** `.../application/reporting.py`

**Implementation notes:**
- Publish ReviewUpdated/MergeUpdated with status.

**References:** MAS §9.6.

**Acceptance criteria:**
- [ ] Events published with correct status
- [ ] ≥90% coverage

---

### SFP-153 [WSW] 🤖 — Merge execution via Git Provider Adapter
**Labels:** platform, ai-agent, workspace-worker | **Deps:** SFP-147, SFP-44 *(B→A)*

**Context:**
Execute the merge via the Git Provider Adapter.

**Requirements:** Execute the merge through the Git Provider Adapter (SFP-44); report `MergeUpdated`.

**Files to create/modify:** `.../application/merge_executor.py`

**Implementation notes:**
- Only on explicit RequestMerge (SFP-147); report MergeUpdated; conflicts are normal failures (ID-068).

**References:** ID-035.

**Acceptance criteria:**
- [ ] Merge executed via the adapter; outcome reported
- [ ] ≥90% coverage

---

### SFP-154 [WSW] 🤖 — Workspace Worker observability hooks
**Labels:** platform, ai-agent, workspace-worker | **Deps:** SFP-143

**Context:**
Emit execution telemetry.

**Requirements:** Emit execution metrics/logs through `sfp-observability` (duration, failures, agent metrics).

**Files to create/modify:** `.../application/observability.py`

**Implementation notes:**
- Duration, failures, agent metrics via sfp-observability with correlation_id (ID-050).

**References:** ID-050.

**Acceptance criteria:**
- [ ] Execution events logged with correlation_id
- [ ] ≥90% coverage

---

## CI-CD Epic 🤖

### SFP-155 [CICD] 🤖 — ci workflow: lint (ruff)
**Labels:** platform, ai-agent, ci-cd | **Deps:** SFP-8

**Context:**
Lint gate on PRs and main.

**Requirements:** GitHub Actions job running `ruff check` + `ruff format --check` on PR and main.

**Files to create/modify:** `.github/workflows/ci.yml`

**Implementation notes:**
- ruff check + ruff format --check; gate the PR (ID-062).

**References:** ID-062.

**Acceptance criteria:**
- [ ] Lint job runs and gates PRs
- [ ] Workflow passes on a clean tree

---

### SFP-156 [CICD] 🤖 — ci workflow: typecheck (mypy)
**Labels:** platform, ai-agent, ci-cd | **Deps:** SFP-9

**Context:**
Type-check gate.

**Requirements:** CI job running `mypy`.

**Files to create/modify:** `.github/workflows/ci.yml`

**Implementation notes:**
- mypy; gate the PR (ID-062).

**References:** ID-062.

**Acceptance criteria:**
- [ ] Typecheck job runs and gates PRs

---

### SFP-157 [CICD] 🤖 — ci workflow: test + coverage gate
**Labels:** platform, ai-agent, ci-cd | **Deps:** SFP-10

**Context:**
Test + coverage gate.

**Requirements:** CI job running `pytest --cov --cov-fail-under=90`.

**Files to create/modify:** `.github/workflows/ci.yml`

**Implementation notes:**
- pytest --cov --cov-fail-under=90 (ID-049); path-filtered per project on PR.

**References:** ID-049, ID-062.

**Acceptance criteria:**
- [ ] Tests run; coverage gate enforced

---

### SFP-158 [CICD] 🤖 — ci workflow: integration tests (LocalStack)
**Labels:** platform, ai-agent, ci-cd | **Deps:** SFP-60, SFP-157

**Context:**
Integration-tests gate.

**Requirements:** CI job running integration tests against LocalStack + Postgres.

**Files to create/modify:** `.github/workflows/ci.yml`

**Implementation notes:**
- Run against LocalStack + Postgres (ID-054); full run on main.

**References:** ID-054, ID-062.

**Acceptance criteria:**
- [ ] Integration tests run in CI with LocalStack

---

### SFP-159 [CICD] 🤖 — release workflow: build + push (ECR)
**Labels:** platform, ai-agent, ci-cd | **Deps:** SFP-75

**Context:**
Build + push on tag.

**Requirements:** On a directory-prefixed tag, build the service image and push to ECR.

**Files to create/modify:** `.github/workflows/release.yml`

**Implementation notes:**
- Directory-prefixed tag (ID-056) → build image → push to ECR (SFP-75).

**References:** ID-056, ID-062.

**Acceptance criteria:**
- [ ] Tag triggers build + push to the service ECR repo

---

### SFP-160 [CICD] 🤖 — release workflow: deploy (Pulumi + ECS)
**Labels:** platform, ai-agent, ci-cd | **Deps:** SFP-159

**Context:**
Deploy on tag.

**Requirements:** After build, apply Pulumi and deploy the service to ECS.

**Files to create/modify:** `.github/workflows/release.yml`

**Implementation notes:**
- After build: pulumi up for the service → ECS deploy (ID-015/056).

**References:** ID-056, ID-062.

**Acceptance criteria:**
- [ ] Service deployed to ECS on tag

---

### SFP-161 [CICD] 🤖 — infrastructure workflow: pulumi preview/up
**Labels:** platform, ai-agent, ci-cd | **Deps:** SFP-66

**Context:**
Infra changes via PR.

**Requirements:** On PR, `pulumi preview`; on merge to main, `pulumi up`.

**Files to create/modify:** `.github/workflows/infrastructure.yml`

**Implementation notes:**
- pulumi preview on PR, pulumi up on merge to main (ID-014).

**References:** ID-062.

**Acceptance criteria:**
- [ ] Preview on PR; apply on merge

---

### SFP-162 [CICD] 🤖 — Reusable workflow templates (workflow_call)
**Labels:** platform, ai-agent, ci-cd | **Deps:** SFP-155

**Context:**
Shared workflow templates.

**Requirements:** Reusable workflow templates so every service/package shares identical ci/release steps.

**Files to create/modify:** `.github/workflows/reusable/*.yml`

**Implementation notes:**
- workflow_call so every service uses identical ci/release steps (uniformity, ID-062).

**References:** ID-062.

**Acceptance criteria:**
- [ ] Services call the shared templates
- [ ] Uniformity enforced

---

## VALIDATION Epic — MAS §12.7 as integration tests 🤖

### SFP-163 [VAL] 🤖 — Duplicate delivery → idempotency holds
**Labels:** platform, ai-agent, validation | **Deps:** SFP-101, SFP-93

**Context:**
MAS §12.7 scenario as an integration test.

**Requirements:** Integration test: deliver the same message twice; assert no duplicate business effect.

**Files to create/modify:** `tests/integration/test_idempotency.py`

**Implementation notes:**
- Deliver the same message twice; assert a single business effect via the ledger (ID-011).

**References:** ID-011.

**Acceptance criteria:**
- [ ] Duplicate delivery produces a single business effect

---

### SFP-164 [VAL] 🤖 — Crash mid-outbox → no duplicate business effect
**Labels:** platform, ai-agent, validation | **Deps:** SFP-92

**Context:**
MAS §12.7 scenario.

**Requirements:** Integration test: simulate a crash between outbox publish and mark; assert no duplicate effect on retry.

**Files to create/modify:** `tests/integration/test_outbox.py`

**Implementation notes:**
- Crash between publish and mark; assert no duplicate effect on retry (ID-053).

**References:** ID-053.

**Acceptance criteria:**
- [ ] Re-publish does not duplicate business effects

---

### SFP-165 [VAL] 🤖 — Back-pressure → queue absorbs
**Labels:** platform, ai-agent, validation | **Deps:** SFP-98

**Context:**
MAS §12.7 scenario.

**Requirements:** Integration test: flood a queue; assert it buffers and services remain responsive.

**Files to create/modify:** `tests/integration/test_backpressure.py`

**Implementation notes:**
- Flood a queue; assert it buffers and services stay responsive (ID-044).

**References:** ID-044.

**Acceptance criteria:**
- [ ] Queue depth absorbs load; no producer blocking

---

### SFP-166 [VAL] 🤖 — Worker crash/restart → recover from durable state
**Labels:** platform, ai-agent, validation | **Deps:** SFP-143, SFP-131

**Context:**
MAS §12.7 scenario.

**Requirements:** Integration test: kill a worker mid-job; assert workflow recovers correctly from durable state.

**Files to create/modify:** `tests/integration/test_recovery.py`

**Implementation notes:**
- Kill a worker mid-job; assert recovery from durable state (MAS §11.13).

**References:** MAS §11.13.

**Acceptance criteria:**
- [ ] Workflow state consistent after restart

---

### SFP-167 [VAL] 🤖 — Scheduler capacity/admission enforced
**Labels:** platform, ai-agent, validation | **Deps:** SFP-128

**Context:**
MAS §12.7 scenario.

**Requirements:** Integration test: exceed capacity; assert admission control caps concurrency.

**Files to create/modify:** `tests/integration/test_scheduler.py`

**Implementation notes:**
- Exceed capacity; assert admission caps concurrency (ID-061).

**References:** ID-061.

**Acceptance criteria:**
- [ ] Concurrency never exceeds the ceiling

---

### SFP-168 [VAL] 🤖 — Closed UserInteraction not reopened
**Labels:** platform, ai-agent, validation | **Deps:** SFP-112

**Context:**
MAS §12.7 scenario.

**Requirements:** Integration test: message a closed interaction; assert it stays closed and a new interaction is started.

**Files to create/modify:** `tests/integration/test_closed_interaction.py`

**Implementation notes:**
- Message a closed interaction; assert it stays closed (MAS §9.4).

**References:** MAS §9.4.

**Acceptance criteria:**
- [ ] Closed interaction never reopens

---

### SFP-169 [VAL] 🤖 — Merge conflict handling
**Labels:** platform, ai-agent, validation | **Deps:** SFP-153

**Context:**
MAS §12.7 scenario.

**Requirements:** Integration test: trigger a merge conflict; assert it is handled as a normal failure (not BLOCKED) per ID-068.

**Files to create/modify:** `tests/integration/test_merge_conflict.py`

**Implementation notes:**
- Trigger a merge conflict; assert normal-failure handling, not BLOCKED (ID-068).

**References:** MAS §11.6, ID-068.

**Acceptance criteria:**
- [ ] Merge conflict handled without escalation to BLOCKED

---

### SFP-170 [VAL] 🤖 — E2E happy path: ticket→plan→code→review→merge→deploy
**Labels:** platform, ai-agent, validation | **Deps:** SFP-132, SFP-143

**Context:**
MAS §12.7 end-to-end scenario.

**Requirements:** End-to-end integration test of the full happy path.

**Files to create/modify:** `tests/integration/test_e2e_happy_path.py`

**Implementation notes:**
- A ticket flows ticket→plan→code→review→merge→deploy→COMPLETED.

**References:** MAS §12.7.

**Acceptance criteria:**
- [ ] A ticket flows through to `COMPLETED` end-to-end

---

# Dependency invariant check

Rule: no `manual-core` ticket (SFP-1..SFP-63) may depend on a `platform` ticket (SFP-64..SFP-170). `platform` → `manual-core` edges are allowed and marked *(B→A)*. This guarantees the Manual Core is a self-contained, manually runnable subgraph (the bootstrap target).
