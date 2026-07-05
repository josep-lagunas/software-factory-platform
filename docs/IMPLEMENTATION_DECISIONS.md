# Software Factory Platform (SFP) — Implementation Decisions

Status: **Canonical**
Version: 1.0
Date: 2026-07-03
Author: Josep Lagunas

This document is the canonical catalogue of SFP implementation decisions (ID-001 … ID-072). It is derived from the Master Architecture Specification and must not contradict it; where a decision and the MAS conflict, the MAS prevails. Each entry records context, decision, rationale, alternatives considered, consequences, references, and affected components. The final section lists Open Validation Items to close during implementation.

---

## ID-001

### Title
Use Python for backend services and platform runtime components.

### Status
Accepted

### Context
The platform requires independently deployable services, shared frameworks, typed contracts, infrastructure tooling, and agent/runtime integration.

### Decision
Implement SFP backend services, shared frameworks, infrastructure tooling, and agent runtime integration primarily in Python.

### Rationale
Python aligns with FastAPI, Pydantic, SQLAlchemy, Alembic, Pulumi, async service development, and the Claude Agent SDK ecosystem.

### Alternatives Considered
- TypeScript: not selected as primary backend/runtime language.
- Polyglot services: rejected for v0 due to operational and packaging complexity.

### Consequences
Positive: consistent implementation language and shared packages.  
Negative: runtime performance must be managed through async execution and horizontal scaling.

### References
Master Architecture Specification Chapters 9 and 10.

### Affected Components
Shared Framework, all services, Infrastructure, Shared Packages.

---

## ID-002

### Title
Use a monorepository with independent service projects.

### Status
Accepted

### Context
SFP contains multiple services plus shared contracts and common frameworks.

### Decision
Use a monorepository. Each service is implemented as an independent Python project with its own source, tests, infrastructure, packaging metadata, and README.

### Rationale
A monorepo simplifies coordinated changes while preserving service boundaries through project structure.

### Alternatives Considered
- Repository per service: deferred/rejected for v0 due to coordination overhead.
- Single application without internal boundaries: rejected because it weakens architecture.

### Consequences
Positive: easier shared contracts and framework evolution.  
Negative: requires discipline to avoid accidental coupling.

### References
Master Architecture Specification Chapter 10.

### Affected Components
Infrastructure, Shared Packages, all services.

---

## ID-003

### Title
Use FastAPI for synchronous query/API capabilities.

### Status
Accepted

### Context
Business workflows are asynchronous, but read/query capabilities may be synchronous.

### Decision
Implement synchronous query/API capabilities using FastAPI.

### Rationale
FastAPI fits Python, async execution, Pydantic schemas, typed request/response models, and lightweight APIs.

### Alternatives Considered
- Flask: rejected in favor of stronger typing and async support.
- gRPC: not selected for v0.
- Message-only system: rejected because read/query APIs are still needed.

### Consequences
Positive: simple API implementation and OpenAPI support.  
Negative: must not become a synchronous orchestration path.

### References
Master Architecture Specification Chapters 3, 4, 9.

### Affected Components
Identity Service, Orchestrator, Communication Service, External Events Service, Shared Framework.

---

## ID-004

### Title
Use Pydantic for platform contracts and schemas.

### Status
Accepted

### Context
Commands, events, external event wrappers, and API/query schemas require typed validation.

### Decision
Use Pydantic models for platform contracts and API schemas.

### Rationale
Pydantic provides validation, serialization, typed models, JSON compatibility, and strong Python ecosystem support.

### Alternatives Considered
- Dataclasses: weaker validation and serialization.
- Protobuf: deferred due to schema tooling overhead.
- Raw dictionaries: rejected because contracts must be explicit.

### Consequences
Positive: validated contracts and easier JSON serialization.  
Negative: contract evolution must be managed carefully.

### References
Master Architecture Specification Chapter 5.

### Affected Components
Shared Contracts, Message Bus, API/query endpoints, all services.

---

## ID-005

### Title
Use PostgreSQL as primary persistence technology.

### Status
Accepted

### Context
SFP needs durable business state, relational relationships, workflow state, idempotency records, and operational ledgers.

### Decision
Use PostgreSQL as the reference persistence technology for service-owned durable state.

### Rationale
PostgreSQL supports transactions, relational consistency, strong querying, operational familiarity, and SQLAlchemy/Alembic tooling.

### Alternatives Considered
- DynamoDB: considered but not selected as primary v0 persistence.
- Event-store-only persistence: rejected for v0.
- Mixed persistence per service: deferred.

### Consequences
Positive: mature persistence and strong consistency.  
Negative: migration and ownership discipline are required.

### References
Master Architecture Specification Chapters 6, 7, 10.

### Affected Components
Identity Service, Communication Service, Orchestrator, External Events Service, Infrastructure.

---

## ID-006

### Title
Use SQLAlchemy for ORM/persistence mapping.

### Status
Accepted

### Context
Domain aggregates must be persisted without making transport contracts database models.

### Decision
Use SQLAlchemy for ORM entities and persistence mapping.

### Rationale
SQLAlchemy supports PostgreSQL, transactional repositories, explicit persistence models, and Alembic migrations.

### Alternatives Considered
- Raw SQL everywhere: too much boilerplate.
- Django ORM: not aligned with FastAPI service architecture.
- Direct mapping from commands/events to rows: rejected.

### Consequences
Positive: explicit ORM models and repository implementation.  
Negative: developers must keep contracts, domain objects, and ORM entities separate.

### References
Master Architecture Specification Chapters 6 and 7.

### Affected Components
Orchestrator, Identity Service, Communication Service, External Events Service, Persistence Layer.

---

## ID-007

### Title
Do not map commands/events directly to ORM entities.

### Status
Accepted

### Context
A concern was raised that commands and events might be treated as database entities.

### Decision
Commands and events are transport contracts. ORM entities are persistence/domain state. Application handlers explicitly translate incoming events into domain changes.

### Rationale
This preserves separation between provider DTOs, native platform contracts, application handlers, domain model, and PostgreSQL persistence.

### Alternatives Considered
- Automatic event-to-row persistence: rejected.
- Reusing command/event schemas as ORM models: rejected.

### Consequences
Positive: safer contract and persistence evolution.  
Negative: more explicit application mapping code is required.

### References
Master Architecture Specification Chapters 4, 5, 6, 7. Uploaded transcript contains the explicit pipeline: provider DTOs -> adapters -> native commands/events -> handlers -> domain model/ORM -> PostgreSQL.

### Affected Components
Shared Contracts, Orchestrator, Communication Service, Workspace Worker, Persistence Layer.

---

## ID-008

### Title
Use Alembic for database migrations.

### Status
Accepted

### Context
PostgreSQL schemas require version-controlled evolution.

### Decision
Use Alembic for database migrations.

### Rationale
Alembic is the standard migration tool for SQLAlchemy-based Python services.

### Alternatives Considered
- Manual SQL migration management: rejected.
- ORM auto-create at startup: rejected for production.
- External migration services: not selected for v0.

### Consequences
Positive: deterministic, reviewable schema changes.  
Negative: migration ordering and service ownership must be managed.

### References
Master Architecture Specification Chapters 7 and 10.

### Affected Components
Identity Service, Communication Service, Orchestrator, External Events Service, Infrastructure.

---

## ID-009

### Title
Implement Message Bus on SNS and SQS.

### Status
Accepted

### Context
The architecture defines a Message Bus abstraction and needs a concrete v0 transport.

### Decision
Implement the Message Bus using AWS SNS for fan-out and SQS for buffering, retry, delivery, and back-pressure.

### Rationale
SNS/SQS provides managed asynchronous messaging, independent consumers, retries, and DLQs.

### Alternatives Considered
- Kafka: rejected/deferred due to operational complexity.
- RabbitMQ: not selected for AWS-first v0.
- Direct HTTP service calls: rejected for business workflows.
- Database polling: rejected as primary messaging.

### Consequences
Positive: managed async transport and queue-based elasticity.  
Negative: at-least-once delivery requires idempotency.

### References
Master Architecture Specification Chapter 4.

### Affected Components
Shared Messaging Framework, all services, Infrastructure.

---

## ID-010

### Title
Use Standard SNS/SQS and enforce ordering in workflow logic.

### Status
Accepted

### Context
Business correctness must not depend on transport ordering.

### Decision
Use Standard SNS/SQS as the reference implementation. Required ordering is enforced by workflow state, policies, and idempotency.

### Rationale
Workflow logic is the correct place to validate state transitions.

### Alternatives Considered
- FIFO queues everywhere: rejected as default.
- Global ordering: rejected as unnecessary and brittle.

### Consequences
Positive: simpler scaling and less transport coupling.  
Negative: consumers must handle duplicate/out-of-order messages.

### References
Master Architecture Specification Chapters 4 and 8.

### Affected Components
Messaging Framework, Orchestrator, Workflow Engine, Policy Engine.

---

## ID-011

### Title
Use service-owned Message Ledgers for idempotency.

### Status
Accepted

### Context
SNS/SQS provides at-least-once delivery.

### Decision
Every message-consuming service maintains a service-owned message ledger for idempotency.

### Rationale
Exactly-once business processing is achieved at the application/service level, not by transport.

### Alternatives Considered
- Rely on exactly-once messaging: rejected.
- Central message ledger: rejected due to shared ownership.
- Ignore duplicates: rejected.

### Consequences
Positive: retry-safe processing.  
Negative: ledger retention and cleanup policies are required.

### References
Master Architecture Specification Chapters 4 and 11.

### Affected Components
Shared Messaging Framework, all message consumers, Persistence Layer.

---

## ID-012

### Title
Use Dead Letter Queues for failed message processing.

### Status
Accepted

### Context
Persistent failures must not be silently discarded.

### Decision
Each SQS queue owns a Dead Letter Queue.

### Rationale
DLQs give operational visibility and isolate poison messages.

### Alternatives Considered
- Infinite retries: rejected.
- Drop failed messages: rejected.
- Central DLQ: rejected due to service ownership.

### Consequences
Positive: clear failure handling.  
Negative: requires DLQ monitoring and replay/runbook decisions.

### References
Master Architecture Specification Chapters 4 and 11.

### Affected Components
Messaging Infrastructure, all services, Observability.

---

## ID-013

### Title
Use JSON serialization for transported contracts.

### Status
Accepted

### Context
Commands, events, and external event wrappers need a common serialization format.

### Decision
Use JSON as the reference serialization format for Pydantic contracts.

### Rationale
JSON is simple, language-neutral, observable, and compatible with HTTP and queue payloads.

### Alternatives Considered
- Protobuf: deferred.
- Avro: deferred.
- Python-specific serialization: rejected.

### Consequences
Positive: easy debugging and low tooling overhead.  
Negative: less compact than binary formats.

### References
Master Architecture Specification Chapters 4 and 5.

### Affected Components
Shared Contracts, Message Bus, Message Handler, all services.

---

## ID-014

### Title
Use Pulumi for Infrastructure as Code.

### Status
Accepted

### Context
Infrastructure must be reproducible and version-controlled.

### Decision
Use Pulumi as the IaC tool.

### Rationale
Pulumi supports Python, typed infrastructure code, AWS resource provisioning, and stack configuration.

### Alternatives Considered
- Terraform: not selected.
- Manual AWS console changes: rejected.
- Raw CloudFormation: not selected.

### Consequences
Positive: infrastructure in Python and reproducible stacks.  
Negative: requires Pulumi state and team familiarity.

### References
Master Architecture Specification Chapter 10.

### Affected Components
Infrastructure, all services, CI/CD.

---

## ID-015

### Title
Use AWS ECS as v0 container orchestration.

### Status
Accepted

### Context
Services and workers must be independently deployable and scalable.

### Decision
Use AWS ECS (Fargate) as the reference container orchestration platform for v0, in region eu-west-1. Long-running services (External Events, Identity, Communication, Orchestrator) run as ECS services. Workspace Worker job execution uses AWS Batch on Fargate (ECS/Fargate compute) to realize the task-per-job execution model (ID-060, ID-061): each command runs as an ephemeral Fargate task, with Batch providing the job queue, per-job containers, scale-to-zero, and retries.

### Rationale
ECS provides managed container execution and simpler operations than Kubernetes for v0. Batch on Fargate is the purpose-built mechanism for the Workspace Worker's ephemeral, one-container-per-job execution (Model A1), removing the need for a custom dispatcher or custom scaling policy while keeping ECS/Fargate as the compute. eu-west-1 (Ireland) is the v0 region, pinning pricing and data residency.

### Alternatives Considered
- Kubernetes/EKS: deferred due to complexity.
- Lambda-only services: rejected as primary runtime for worker/agent execution.
- Bare EC2: rejected.
- Pure ECS service + custom SQS→RunTask dispatcher for the Workspace Worker: rejected in favor of Batch, which provides dispatch and scale-to-zero natively.

### Consequences
Positive: managed container deployment, independent scaling, and native per-job execution for the Workspace Worker.  
Negative: AWS-specific runtime implementation; Batch is an additional service (still ECS/Fargate compute).

### References
ID-060, ID-061. Master Architecture Specification §9.6, §10.5.

### Affected Components
Infrastructure and all runtime services.

---

## ID-016

### Title
Use AWS Secrets Manager and injected configuration.

### Status
Accepted

### Context
Services require provider credentials, database credentials, webhook secrets, and runtime configuration.

### Decision
Store secrets centrally in AWS Secrets Manager and inject secret references/configuration into services.

### Rationale
Centralized secret management reduces leakage risk and supports provider authentication.

### Alternatives Considered
- Hardcoded secrets: rejected.
- Plain env files with real secrets: rejected.
- Service-specific ad hoc storage: rejected.

### Consequences
Positive: stronger security posture.  
Negative: IAM and local development strategies must be defined.

### References
Master Architecture Specification Chapter 10.

### Affected Components
External Events Service, Communication Service, Workspace Worker, Infrastructure, CI/CD.

---

## ID-017

### Title
Use GitHub Actions for CI/CD.

### Status
Accepted

### Context
SFP requires version-controlled build, test, and deployment automation.

### Decision
Use GitHub Actions for CI/CD pipelines.

### Rationale
It integrates naturally with GitHub repositories and pull request workflows.

### Alternatives Considered
- Jenkins: rejected due to operational overhead.
- Manual deployment: rejected.
- AWS CodePipeline: not selected for v0.

### Consequences
Positive: PR-oriented automation and simple repository integration.  
Negative: secure AWS authentication strategy is required.

### References
Master Architecture Specification Chapter 10.

### Affected Components
CI/CD, Infrastructure, all services.

---

## ID-018

### Title
Use Claude Agent SDK as Agent Runtime implementation basis.

### Status
Accepted

### Context
Workspace Worker requires coding/review agents with tool use, sessions, and codebase interaction.

### Decision
Use Claude Agent SDK as the implementation basis for the Agent Runtime.

### Rationale
The SDK provides tool use, session management, skills/subagents, and Claude Code ecosystem integration without building a custom runtime.

### Alternatives Considered
- Custom provider abstraction from scratch: superseded for v0.
- Direct model API calls: rejected as insufficient.
- Anthropic-hosted-only runtime: rejected due to provider lock-in.

### Consequences
Positive: faster implementation and mature coding-agent capabilities.  
Verification: the routing mechanism is confirmed — the Claude Agent SDK honors `ANTHROPIC_BASE_URL` (env var or `ClaudeAgentOptions(env={...})`), so directing the SDK at an Anthropic-compatible provider is a supported, config-driven capability.  
Residual gate: runtime fidelity of a non-Anthropic compatible endpoint for the full agentic loop (tool-calling) is an empirical integration validation, performed once before the Workspace Worker runs production jobs (see ID-019). Because the provider is selected by configuration, a failed validation falls back to Anthropic-direct Claude with no code or architecture change; architecture correctness is therefore not at risk.

### References
Conversation transcript: Claude Agent SDK + Anthropic-compatible provider decision. Master Architecture Specification Chapter 9.

### Affected Components
Workspace Worker, Agent Runtime, Shared Framework, Infrastructure.

---

## ID-019

### Title
Use configurable Anthropic-compatible model endpoints.

### Status
Accepted

### Context
The Agent Runtime should support provider flexibility without losing Claude Agent SDK capabilities.

### Decision
Use Claude Agent SDK with a configurable Anthropic-compatible endpoint. Supported examples include Anthropic Claude, Z.ai GLM 5.1, and future compatible providers.

### Rationale
This reduces vendor lock-in while preserving Agent SDK/Claude Code capabilities.

### Alternatives Considered
- Anthropic-hosted Claude only: rejected.
- OpenAI-compatible Z.ai endpoint for Claude Agent SDK: rejected for this path because the SDK expects Anthropic-compatible semantics.
- Fully custom multi-provider runtime: superseded for v0.

### Consequences
Positive: lower lock-in and potential lower costs.  
Residual gate: runtime provider compatibility and tool-call behavior through the Z.ai GLM Anthropic-compatible endpoint is an empirical integration validation — a pre-implementation test that runs the Claude Agent SDK against the GLM endpoint and exercises tool-calling. Validation outcome decides whether v0 runs on GLM or on Anthropic-direct Claude; switching is a configuration change (`ANTHROPIC_BASE_URL`), so neither code nor architecture changes either way.

### References
Conversation transcript: Z.ai GLM 5.1 / Claude Agent SDK discussion.

### Affected Components
Workspace Worker, Agent Runtime, Infrastructure, Configuration.

---

## ID-020

### Title
Configure model routing through environment/global Claude settings.

### Status
Accepted

### Context
Claude Agent SDK can be routed to compatible providers by configuration.

### Decision
Support provider routing through configuration such as `ANTHROPIC_BASE_URL`, `ANTHROPIC_DEFAULT_SONNET_MODEL`, and related environment variables or Claude settings.

### Rationale
Provider switching should be configuration-driven, not a code change.

### Alternatives Considered
- Hardcoded provider/model values: rejected.
- Code changes for provider switching: rejected.
- Only global developer machine config: useful locally but insufficient for deployed services.

### Consequences
Positive: model/provider switching via config.  
Negative: startup validation is required to avoid misrouting.

### References
Conversation transcript: GLM 5.1 configuration discussion.

### Affected Components
Workspace Worker, Agent Runtime, Configuration, Infrastructure, Secrets.

---

## ID-021

### Title
Planner output uses deterministic JSON PR task structures.

### Status
Accepted

### Context
Planner must transform a Jira ticket into implementable units.

### Decision
Planner returns deterministic JSON composed of small, self-contained pull request tasks. Each task includes title, objective, scope, likely affected files/modules when known, acceptance criteria, testing requirements, dependencies, risks, and implementation notes.

### Rationale
Deterministic JSON makes planner output machine-consumable and reduces ambiguity.

### Alternatives Considered
- Free-form text plan: rejected.
- Single large PR per Jira ticket: rejected.
- Interactive planner/coder clarification by default: not selected for v0.

### Consequences
Positive: easier automation and Blueprint generation.  
Negative: schema design and validation are required.

### References
Conversation transcript: initial multi-agent workflow and planner output requirements.

### Affected Components
Orchestrator, Planner/Internal PRSpecification generation, Shared Contracts, Blueprint.

---

## ID-022

### Title
Coder Agent implements one PR task and its tests.

### Status
Accepted

### Context
Implementation work must be bounded and reviewable.

### Decision
Coder Agent receives one pull request task, implements code changes, adds or updates unit tests, satisfies acceptance criteria, and submits the PR for review.

### Rationale
Small PRs are easier to review and align with normal engineering practice.

### Alternatives Considered
- One Jira ticket equals one large PR: rejected.
- Tests optional: rejected.
- Coder bypasses review: rejected.

### Consequences
Positive: smaller PRs and better quality.  
Negative: precise PR task specifications are required.

### References
Conversation transcript: Coder Agent responsibilities.

### Affected Components
Workspace Worker, Agent Runtime, Orchestrator, Repository Manager.

---

## ID-023

### Title
Reviewer Agent performs requirement, test, and quality review.

### Status
Accepted

### Context
Automated implementation requires automated review before user validation.

### Decision
Reviewer Agent checks requirement compliance, unit test coverage, quality, maintainability, readability, correctness, security, and edge cases. It creates review comments and approves only when requirements are met and comments resolved.

### Rationale
This creates an automated quality gate before user approval.

### Alternatives Considered
- User-only review: rejected for target automation.
- Coder self-review only: rejected.
- Merge without review: rejected.

### Consequences
Positive: improved automated quality control.  
Negative: reviewer prompt/runtime quality becomes important.

### References
Conversation transcript: Reviewer Agent responsibilities.

### Affected Components
Workspace Worker, Agent Runtime, Orchestrator, Git Provider Adapter.

---

## ID-024

### Title
User approval remains required before merge.

### Status
Accepted

### Context
The system automates implementation but must preserve user accountability.

### Decision
After reviewer approval, the Orchestrator notifies the user with a PR link. The user approves, rejects, or requests changes. Merge readiness requires reviewer comments resolved and user approval — **except for LEVEL_1_INTERNAL changes** (per ID-067), which have no runtime or user-visible impact and may auto-merge after the Reviewer and CI/unit tests pass, without human approval.

### Rationale
This preserves governance and final accountability for any change that can affect behavior, while allowing truly low-impact changes (documentation, tests-only, refactor, formatting) to flow through automatically — reducing the human bottleneck without reducing safety. Mis-classification is mitigated by the Reviewer (which checks `no_unrelated_changes`) and the "when in doubt, choose the higher validation level" rule (ID-067).

### Alternatives Considered
- Fully autonomous merge for all PRs: rejected for v0.
- User approval before every automated step: rejected because it weakens automation.
- User-only final validation without automated review: rejected.
- Universal human approval with no LEVEL_1 exception: rejected as an unnecessary bottleneck for no-impact changes.

### Consequences
Positive: safer automation and accountability where it matters; LEVEL_1 changes are fully automated end-to-end.  
Negative: correct validation-level classification becomes safety-critical; a mis-classified LEVEL_1 change could auto-merge.

### References
Conversation transcript: User approval workflow.

### Affected Components
Orchestrator, Communication Service, Workspace Worker, Git Provider Adapter.

---

## ID-025

### Title
Use GitHub pull requests as the implementation review unit.

### Status
Accepted

### Context
The workflow needs a concrete implementation artifact for code, review comments, approvals, and merge.

### Decision
Use GitHub Pull Requests as the primary implementation review unit in v0.

### Rationale
The entire workflow is PR-centric: planner slices work into PR tasks, coder implements PRs, reviewer comments on PRs, and users approve PRs.

### Alternatives Considered
- Direct commits to main: rejected.
- Patch files without PRs: rejected.
- Non-GitHub providers: deferred behind provider abstractions.

### Consequences
Positive: standard engineering workflow and traceability.  
Negative: v0 implementation is GitHub-oriented.

### References
Conversation transcript: initial workflow uses pull requests throughout.

### Affected Components
Workspace Worker, Git Provider Adapter, Orchestrator, Shared Contracts.

---

## ID-026

### Title
Use explicit provider DTO -> native contract -> domain translation.

### Status
Accepted

### Context
External provider payloads must not leak into domain state.

### Decision
Provider payloads are represented as provider DTOs/schemas, interpreted by owning service adapters, converted to native SFP commands/events, then handled by application handlers.

### Rationale
This isolates vendor schemas and protects the domain model.

### Alternatives Considered
- Store raw provider payloads as business facts: rejected.
- Share provider DTOs globally: rejected.
- Let External Events Service interpret provider semantics: rejected.

### Consequences
Positive: clean adapter boundary.  
Negative: provider adapters must be implemented and tested.

### References
Conversation transcript: provider DTO -> adapter -> native event/command -> handler -> domain/ORM -> PostgreSQL pipeline.

### Affected Components
External Events Service, Communication Service, Orchestrator, Workspace Worker, Shared Contracts.

---

## ID-027

### Title
Use Slack as v0 communication provider.

### Status
Accepted

### Context
Communication Service needs a concrete provider for user notifications and input.

### Decision
Implement Slack as the v0 communication provider.

### Rationale
Slack supports threads, user notifications, and common engineering-team workflows.

### Alternatives Considered
- Microsoft Teams: deferred.
- Email: deferred.
- WhatsApp: deferred.
- Multi-provider v0: rejected/deferred.

### Consequences
Positive: concrete v0 provider and thread model.  
Negative: v0 communication UX depends on Slack.

### References
Master Architecture Specification Chapters 6 and 9.

### Affected Components
Communication Service, External Events Service, Identity Service, Configuration.

---

## ID-028

### Title
Use `/webhooks/{endpoint_id}` for external webhook ingress.

### Status
Accepted

### Context
External providers need authenticated ingress while keeping provider interpretation out of infrastructure.

### Decision
Expose webhook ingress using endpoint identifiers such as `/webhooks/{endpoint_id}`. Endpoint configuration resolves provider, authentication strategy, secret reference, and endpoint status.

### Rationale
One generic ingress service can authenticate and wrap external events without understanding business semantics.

### Alternatives Considered
- Provider-specific endpoint handlers in business services: rejected.
- Hardcoded provider endpoints: rejected.
- Business interpretation in ingress: rejected.

### Consequences
Positive: generic ingress and centralized authentication.  
Negative: endpoint configuration persistence is required.

### References
Master Architecture Specification Chapter 9.

### Affected Components
External Events Service, Infrastructure, Secrets, Communication Service, Orchestrator.

---

## ID-029

### Title
Use injected authentication strategies for webhook validation.

### Status
Accepted

### Context
Different providers use different authentication mechanisms.

### Decision
Implement webhook authentication through an Authentication Strategy Factory. Strategies receive injected secrets and do not load secrets themselves.

### Rationale
This keeps authentication extensible, testable, and independent of provider business interpretation.

### Alternatives Considered
- Hardcoded provider authentication: rejected.
- Let owning services authenticate webhooks: rejected.
- Strategies loading secrets directly: rejected.

### Consequences
Positive: clear security boundary and easier provider onboarding.  
Negative: requires endpoint configuration and secret wiring.

### References
Master Architecture Specification Chapter 9.

### Affected Components
External Events Service, Secrets Manager, Infrastructure, Shared Framework.

---

## ID-030

### Title
Use automatic framework-provided observability.

### Status
Accepted

### Context
All services need consistent logs, metrics, traces, retries, failures, and message timings.

### Decision
Observability is implemented in shared frameworks/middleware rather than directly in business code.

### Rationale
Centralized observability ensures consistency and reduces duplicated instrumentation.

### Alternatives Considered
- Per-service custom observability: rejected.
- No automatic observability: rejected.
- Business code manually emits all telemetry: rejected.

### Consequences
Positive: consistent telemetry.  
Negative: shared framework becomes critical.

### References
Master Architecture Specification Chapters 4, 10, 11.

### Affected Components
Shared Framework, Messaging Framework, all services, Infrastructure.

---

## ID-031

### Title
Use correlation and causation identifiers in message envelopes.

### Status
Accepted

### Context
Distributed workflows require traceability across services.

### Decision
Every transported message includes identifiers such as message_id, idempotency_key, correlation_id, causation_id, message_type, occurred_at, and payload.

### Rationale
These fields enable trace reconstruction, debugging, and observability.

### Alternatives Considered
- Payload-only messages: rejected.
- Service-specific envelopes: rejected.
- Rely only on infrastructure tracing: rejected.

### Consequences
Positive: strong traceability.  
Negative: framework must enforce propagation.

### References
Master Architecture Specification Chapter 4.

### Affected Components
Message Bus, Message Handler, Shared Contracts, Observability.

---

## ID-032

### Title
Use non-interactive coding execution in v0.

### Status
Accepted

### Context
Coding jobs should not pause while waiting for user clarification.

### Decision
Coding execution is non-interactive in v0. If a PRSpecification is insufficient, CodingJob fails with a structured reason instead of pausing.

### Rationale
This keeps worker execution deterministic and prevents execution capacity from being occupied by waiting jobs.

### Alternatives Considered
- Interactive coding sessions: deferred.
- Paused coding jobs: rejected for v0.
- ResumeCodingJob command: rejected for v0.

### Consequences
Positive: simpler execution model.  
Negative: more failures when specifications are insufficient.

### References
Master Architecture Specification Chapters 6 and 9.

### Affected Components
Workspace Worker, Agent Runtime, Orchestrator, Scheduler.

---

## ID-033

### Title
Use ephemeral worktrees and local execution state in Workspace Worker.

### Status
Accepted

### Context
Workers need local state for repository checkout, branches, agent execution, tests, and build artifacts.

### Decision
Workspace Workers may use ephemeral local state such as worktrees, caches, temporary agent context, and build artifacts. None of this is business persistence.

### Rationale
Execution needs local mutable state, but business correctness belongs to Orchestrator state.

### Alternatives Considered
- Persist worker execution state as business state: rejected.
- Shared worker filesystem for correctness: rejected.
- Sticky worker affinity: rejected.

### Consequences
Positive: horizontally scalable workers.  
Negative: cleanup and retry behavior must be implemented.

### References
Master Architecture Specification Chapters 6, 9, 11.

### Affected Components
Workspace Worker, Repository Manager, Agent Runtime, Infrastructure.

---

## ID-034

### Title
Use Repository Manager inside Workspace Worker.

### Status
Accepted

### Context
Coding and review require consistent repository checkout, branch, worktree, and cleanup logic.

### Decision
Implement Repository Manager as an internal Workspace Worker component.

### Rationale
Centralizes repository operations and prevents agent-level duplication.

### Alternatives Considered
- Each agent manages repositories: rejected.
- Orchestrator manages repositories: rejected.
- Separate Git service in v0: not selected.

### Consequences
Positive: reusable repository lifecycle.  
Negative: concurrent execution and cleanup must be carefully implemented.

### References
Master Architecture Specification Chapter 9.

### Affected Components
Workspace Worker, Repository Manager, Git Provider Adapter, Agent Runtime.

---

## ID-035

### Title
Use Git Provider Adapter for outbound GitHub operations.

### Status
Accepted

### Context
Workspace Worker must push branches, create/update PRs, submit reviews, and synchronize branches.

### Decision
Implement outbound source-control operations through a Git Provider Adapter owned by Workspace Worker.

### Rationale
Isolates GitHub API details and preserves future provider flexibility.

### Alternatives Considered
- Agents call GitHub APIs directly: rejected.
- Orchestrator performs Git operations: rejected.
- One shared GitHub service for inbound/outbound semantics: not selected for v0.

### Consequences
Positive: clear boundary and testability.  
Negative: adapter must model GitHub errors carefully.

### References
Master Architecture Specification Chapters 5 and 9.

### Affected Components
Workspace Worker, Git Provider Adapter, Orchestrator.

---

## ID-036

### Title
Inbound GitHub webhooks are interpreted by Orchestrator.

### Status
Accepted

### Context
GitHub webhooks represent workflow-relevant facts while Workspace Worker performs outbound GitHub side effects.

### Decision
Inbound GitHub webhook interpretation belongs to Orchestrator. Outbound GitHub operational interactions belong to Workspace Worker.

### Rationale
Inbound facts affect workflow state and belong to the workflow owner. Outbound side effects are execution.

### Alternatives Considered
- Workspace Worker consumes GitHub webhooks: rejected.
- External Events Service interprets GitHub semantics: rejected.
- Shared GitHub service owns both: not selected.

### Consequences
Positive: clean separation of facts and side effects.  
Negative: two GitHub implementation surfaces must be maintained.

### References
Master Architecture Specification Chapters 5 and 9.

### Affected Components
Orchestrator, Workspace Worker, External Events Service, Shared Contracts.

---

## ID-037

### Title
Create Implementation Decisions before Blueprint generation.

### Status
Accepted

### Context
Blueprint tickets must be deterministic and should not contain unresolved design choices.

### Decision
Create an Implementation Decisions document between the Master Architecture Specification and the Implementation Blueprint.

### Rationale
Architecture defines what the system is. Implementation Decisions define how it is built.

### Alternatives Considered
- Generate Blueprint directly from Master Specification: rejected because implementation ambiguity would leak into tickets.
- Put all implementation details in Master Specification: rejected because the MAS remains architectural.

### Consequences
Positive: more deterministic Blueprint.  
Negative: additional artifact to maintain.

### References
Master Architecture Specification Chapter 12. Conversation damage-control discussion.

### Affected Components
Documentation, Blueprint, Jira Backlog, Engineering Process.

---

## ID-038

### Title
Blueprint tickets must not contain unresolved architecture or implementation choices.

### Status
Accepted

### Context
The Blueprint will generate deterministic Jira implementation tickets.

### Decision
If a ticket requires an architectural decision, the MAS is incomplete. If it requires an implementation decision, IMPLEMENTATION_DECISIONS.md is incomplete.

### Rationale
Ticket assignees should implement, not design.

### Alternatives Considered
- Let assignees choose implementation details ad hoc: rejected.
- Encode uncertainty in tickets: rejected.

### Consequences
Positive: deterministic execution and agent suitability.  
Negative: more upfront specification work.

### References
Master Architecture Specification Chapter 12.

### Affected Components
Implementation Blueprint, Jira Backlog, Engineering Process.

---

## ID-039

### Title
Unit tests are mandatory for agent-generated implementation.

### Status
Accepted

### Context
The Coder Agent must produce reviewable, quality-controlled code.

### Decision
Every coding task must add or update unit tests and satisfy testing requirements from the PR task.

### Rationale
Automated implementation without test coverage would not meet the required quality bar.

### Alternatives Considered
- Tests optional: rejected.
- Reviewer writes tests: rejected; reviewer validates tests.
- User-only test review: insufficient.

### Consequences
Positive: higher quality PRs.  
Negative: test expectations must be explicit in PR specifications.

### References
Conversation transcript: Coder and Reviewer responsibilities.

### Affected Components
Workspace Worker, Agent Runtime, Reviewer Agent, Coder Agent, Blueprint.

---

## ID-040

### Title
Use review comments as the correction mechanism.

### Status
Accepted

### Context
The implementation/review loop needs a deterministic correction path.

### Decision
Reviewer Agent creates review comments. Coder Agent fixes issues or starts discussion. Reviewer resolves comments only after acceptable fixes or agreement.

### Rationale
This mirrors normal PR review behavior.

### Alternatives Considered
- Reviewer directly modifies code: rejected.
- Coder ignores reviewer: rejected.
- User mediates every disagreement: not selected for v0.

### Consequences
Positive: traceable correction loop.  
Negative: comment-thread state tracking is required.

### References
Conversation transcript: Reviewer/Coder loop.

### Affected Components
Workspace Worker, Git Provider Adapter, Orchestrator, Agent Runtime.

---

## ID-041

### Title
Use service-local provider schemas.

### Status
Accepted

### Context
Slack, Jira, GitHub, and GitHub Actions payloads are provider-specific.

### Decision
Provider schemas live inside the service that owns interpretation of that provider. They are not shared platform contracts.

### Rationale
SFP shares business contracts, not vendor contracts.

### Alternatives Considered
- Put all provider schemas in shared contracts: rejected.
- Let External Events Service own provider schemas: rejected.
- Share provider payload semantics across services: rejected.

### Consequences
Positive: provider semantics remain local.  
Negative: repeated DTO patterns may exist across services.

### References
Master Architecture Specification Chapter 5.

### Affected Components
Communication Service, Orchestrator, Shared Contracts, External Events Service.

---

## ID-042

### Title
Use service-owned infrastructure modules.

### Status
Accepted

### Context
Every service owns compute, queues, DLQs, IAM, configuration, and secrets.

### Decision
Implement infrastructure as shared platform modules plus per-service infrastructure modules.

### Rationale
This mirrors service ownership while allowing physical infrastructure sharing.

### Alternatives Considered
- One global module for everything: rejected.
- Duplicated platform infrastructure per service: rejected.
- Manual infrastructure: rejected.

### Consequences
Positive: clear ownership and independent scaling.  
Negative: more modules and conventions to maintain.

### References
Master Architecture Specification Chapter 10.

### Affected Components
Infrastructure, all services.

---

## ID-043

### Title
Use liveness/readiness health endpoints.

### Status
Accepted

### Context
Services must only consume work after safe startup.

### Decision
Each service exposes liveness and readiness capabilities. Only ready services consume messages.

### Rationale
This prevents partially initialized services from processing messages.

### Alternatives Considered
- Single health endpoint: insufficient.
- Consume during startup: rejected.
- Rely only on process state: rejected.

### Consequences
Positive: safer runtime behavior.  
Negative: readiness checks must be implemented correctly.

### References
Master Architecture Specification Chapter 11.

### Affected Components
All services, Infrastructure, ECS.

---

## ID-044

### Title
Use queue depth as back-pressure signal.

### Status
Accepted

### Context
The platform must absorb load without blocking producers or corrupting workflow.

### Decision
Use SQS queue depth as the primary back-pressure signal. Queue depth is accumulated work, not failure.

### Rationale
Messaging infrastructure absorbs spikes while workers consume according to capacity.

### Alternatives Considered
- Block producers by default: rejected.
- Treat queue depth as failure: rejected.
- Synchronous execution admission everywhere: rejected.

### Consequences
Positive: clear scaling signal.  
Negative: queue monitoring and worker scaling policies are needed.

### References
Master Architecture Specification Chapters 4 and 11.

### Affected Components
Messaging Infrastructure, Workspace Worker, Scheduler, Observability.

---

## ID-045

### Title
Use Git as the source of truth for architecture/specification artifacts going forward.

### Status
Accepted

### Context
The conversation became too large to safely serve as the canonical artifact.

### Decision
Persist canonical artifacts such as `MASTER_SPECIFICATION.md`, `IMPLEMENTATION_DECISIONS.md`, Blueprint, ADRs, and Validation documents in Git.

### Rationale
Architecture should be versioned like source code and protected from conversational memory loss.

### Alternatives Considered
- Keep architecture in chat: rejected.
- Generate final docs only at the end: rejected.
- Use Canvas only: considered, but Git workflow is preferred.

### Consequences
Positive: durable source of truth and reviewable history.  
Negative: requires repository discipline.

### References
Conversation transcript: damage-control and export discussion.

### Affected Components
Documentation, Engineering Process, Git Repository.

---

## ID-046

### Title
ReviewPullRequest executes a review pass; the Orchestrator records it as a Review.

### Status
Accepted

### Context
MAS §6.9 defines Review as a domain aggregate scoped to a CodingJob and explicitly decoupled from any source-control provider. The Workspace Worker consumes the ReviewPullRequest command, which operates on a GitHub Pull Request. The mapping between the execution-level review (on the PR) and the domain Review aggregate must be explicit so ownership stays unambiguous.

### Decision
In v0 each CodingJob corresponds to exactly one GitHub Pull Request (PRSpecification:CodingJob:PR = 1:1:1). The Workspace Worker executes one review pass on that PR via ReviewPullRequest and reports the outcome via ReviewUpdated. The Orchestrator records that outcome as one Review on the owning CodingJob. The PR is the external execution artifact of the CodingJob; it is not a domain entity. Multiple review passes on the same PR produce multiple immutable Reviews on the same CodingJob.

### Rationale
This preserves the WHAT/HOW separation: the Orchestrator owns the Review aggregate (WHAT), the Workspace Worker owns PR execution (HOW), and the domain model never depends on a source-control provider.

### Alternatives Considered
- Rename ReviewPullRequest to ReviewCodingJob: rejected; the command names the artifact the worker actually operates on (the PR), consistent with the execution boundary.
- Model the PR as a domain entity: rejected; it would couple the domain to a specific provider, contradicting MAS §6.9 and AP-007.

### Consequences
Positive: unambiguous ownership; domain remains provider-independent.  
Negative: the CodingJob↔PR 1:1 invariant must be enforced by the Workspace Worker and validated by the Orchestrator.

### References
Master Architecture Specification §5.3, §6.9, §9.5, §9.6. ID-022, ID-025, ID-036.

### Affected Components
Orchestrator, Workspace Worker, Git Provider Adapter, Shared Contracts.

---

## ID-047

### Title
Pin Python 3.13 as the project language version.

### Status
Accepted

### Context
ID-001 selects Python but does not pin a version. The stack fixed by other accepted decisions (FastAPI, Pydantic, SQLAlchemy, Alembic, Pulumi in Python, Claude Agent SDK) requires a single compatible baseline across all services and shared packages.

### Decision
Use Python 3.13 as the project baseline. Pin a single minor version in every service and shared package via `requires-python = ">=3.13,<3.14"` in `pyproject.toml`, enforced by the package manager and CI.

### Rationale
As of mid-2026, Python 3.13 is mature, fully supported, and the lowest-risk baseline for broad compatibility across the chosen stack. Pinning one minor version keeps every service, shared package, and the Agent Runtime on exactly one interpreter, avoiding cross-version drift in a monorepo.

### Alternatives Considered
- Python 3.14: valid and newer, but deferred until the full dependency set (notably the Claude Agent SDK and Pulumi Python compatibility) is verified against 3.14.
- A version range (e.g. `>=3.12`): rejected to avoid cross-version drift and non-deterministic execution.

### Consequences
Positive: deterministic interpreter across the monorepo; simpler CI matrix.  
Negative: re-evaluate 3.14 at a future point.  
Verification: the Claude Agent SDK (`requires-python >= 3.10`, no upper cap) and the Pulumi Python SDK (`requires-python >= 3.10`; classifiers list 3.10–3.14) both support Python 3.13. The core dependencies gating this decision are confirmed compatible. The full v0 stack (httpx, FastAPI, Pydantic, SQLAlchemy, Alembic, structlog, ruff, mypy, pytest, pytest-asyncio, tenacity, coverage) also declares `requires-python` with no upper cap below 3.13, so Python 3.13 is confirmed compatible across the entire v0 dependency set.

### References
ID-001, ID-003, ID-004, ID-006, ID-008, ID-014, ID-018. Master Architecture Specification Chapters 9 and 10.

### Affected Components
Shared Framework, all services, Infrastructure, CI/CD.

---

## ID-048

### Title
Use uv as the package and project manager with a single-root workspace lockfile.

### Status
Accepted

### Context
ID-002 mandates a monorepo of independent service and shared-package projects. ID-047 pins Python 3.13. A package manager, workspace model, and lockfile strategy are required so shared packages are consumed by services without publishing, and so resolution is reproducible in CI.

### Decision
Use `uv` as the package and project manager. Model the monorepo as a single `uv` workspace:
- A workspace root `pyproject.toml` declares workspace members (`services/*`, `packages/*`).
- Each service and shared package remains its own project with its own `pyproject.toml` (per ID-002 and the uniform service layout).
- Shared packages (`sfp-contracts`, `sfp-messaging`, etc.) are consumed as workspace path dependencies.
- Maintain a single root `uv.lock` for monorepo-wide reproducible resolution.
- CI installs with `uv sync --frozen` to enforce the lock.
- `uv` also manages the pinned Python 3.13 interpreter (ID-047).

### Rationale
`uv` provides interpreter management, dependency resolution, locking, and virtualenvs in one tool. Workspace path dependencies let shared packages flow to services without an artifact registry. A single root lockfile keeps the whole monorepo on one resolved dependency set.

### Alternatives Considered
- Poetry / Hatch / pip-tools: not selected; `uv` workspaces fit the monorepo model and are faster.
- Per-project lockfiles: rejected for the monorepo in favor of a single root `uv.lock`.
- Publishing shared packages to an index: rejected for v0; workspace path dependencies suffice.

### Consequences
Positive: one tool, one lockfile, reproducible CI, fast installs.  
Negative: workspace feature surface must be respected; team must follow the single-lockfile discipline.

### References
ID-001, ID-002, ID-047, ID-017. Master Architecture Specification Chapter 10. Implementation Notes §3 (Uniform Service Layout) and §4 (Shared Packages).

### Affected Components
Shared Packages, all services, CI/CD, Infrastructure.

---

## ID-049

### Title
Use pytest with pytest-asyncio and enforce a 90% coverage floor.

### Status
Accepted

### Context
ID-039 mandates unit tests for agent-generated code. The messaging framework must run handlers without AWS/SNS/SQS using fake MessageBus and fake MessageContext (Implementation Notes §1), provided by the `sfp-testing` package. A test framework, coverage tooling, and an enforced coverage threshold are required.

### Decision
Use `pytest` as the test framework with `pytest-asyncio` for async handler tests. Use plain `assert` statements. Measure coverage with `coverage.py` via `pytest-cov`. Enforce a minimum coverage floor of 90% per service/project in CI (`pytest --cov --cov-fail-under=90`). Coverage is measured per service or shared package (each is its own project per ID-002).

### Rationale
pytest is the Python ecosystem default and integrates cleanly with the `sfp-testing` fakes. pytest-asyncio covers the async message handlers and FastAPI code paths. An enforced 90% floor keeps the quality bar high for both human-written and agent-generated implementation (ID-022, ID-039). Coverage is a floor, not a target, and must not be gamed.

### Alternatives Considered
- unittest: rejected in favor of pytest fixtures, parametrization, and plain asserts.
- No coverage gate: rejected; ID-039 requires verifiable test coverage.
- 80% floor: rejected as too low for the target quality bar.

### Consequences
Positive: consistent test stack; enforced coverage across services and agent output.  
Negative: 90% is demanding and must be maintained as the codebase grows; coverage discipline is required to avoid gaming.

### References
ID-002, ID-003, ID-022, ID-039, ID-048. Implementation Notes §1 (Testing) and §4 (sfp-testing). Master Architecture Specification Chapter 9.

### Affected Components
sfp-testing, all services, Workspace Worker (agent-generated tests), CI/CD.

---

## ID-050

### Title
v0 observability is structured logging only; OTel metrics and tracing are deferred.

### Status
Accepted

### Context
MAS mandates automatic, framework-provided observability — logging, metrics, and distributed tracing (§4.21, §10.11, §11.12) — and ID-030 places it in shared frameworks, not business code. Implementation Notes §4 defines `sfp-observability`. To minimize v0 cost and operational surface, observability is scoped down for v0 while keeping the full MAS observability model as the architectural target. Every message already carries `correlation_id` and `causation_id` (MAS §4.7/§4.8, ID-031), independent of any trace backend.

### Decision
For v0, ship structured logging only:
- Use `structlog` to emit structured JSON logs to stdout, captured by ECS (`awslogs` driver) into CloudWatch Logs. Logs are the sole telemetry channel in v0.
- Bind `correlation_id` and `causation_id` into every log line so a workflow can be followed across services and queues by filtering logs, even without a distributed-tracing UI.
- The messaging framework records the MAS §4.21 set (published/consumed messages, processing duration, retries, successes, failures, correlation identifiers) as structured log events. The information §4.21 requires is therefore captured in v0 via logs, not via a metrics backend.
- Business code emits telemetry only through `sfp-observability` abstractions and never touches vendor SDKs directly.

Deferred to a later phase (before the platform is considered production-complete): OpenTelemetry metrics, OpenTelemetry distributed tracing, the OTel Collector, the X-Ray / CloudWatch-metrics backend, W3C TraceContext envelope propagation, and OTel instrumentation of FastAPI and httpx. Adding them later requires no rewrite, because logging is already structured and `correlation_id` is already in every log line; tracing layers trace IDs on top.

This is a v0 scope reduction of MAS §4.21/§10.11/§11.12, not an architecture change. The full observability model (logging + metrics + tracing) remains the architectural target.

### Rationale
Logging-only keeps v0 cost and operational surface minimal (no Collector, no X-Ray, no custom-metrics cardinality risk) while still satisfying the MAS recording requirements through structured, correlation-bound logs. Cross-service followability is preserved via `correlation_id` in logs. Deferring OTel is clean because structured logging and correlation identifiers are already in place.

### Alternatives Considered
- Full OTel (metrics + tracing) in v0 (original ID-050): rejected for v0 to cut cost and operational surface.
- Per-service custom instrumentation: rejected (ID-030).
- Direct CloudWatch/X-Ray SDK calls in business code: rejected; violates MAS §4.21/§10.11/§11.12 and AP-007.
- Non-AWS managed backends (Honeycomb/Grafana): deferred; selectable by configuration when tracing is added.

### Consequences
Positive: minimal v0 cost and ops; correlation-bound logs give cross-service followability; OTel adds cleanly later.  
Negative: no metrics time-series or distributed-tracing UI in v0; operational diagnosis relies on log queries until the deferred capabilities are added.

### References
ID-030, ID-031, ID-048, ID-049, ID-051. Master Architecture Specification §4.7, §4.8, §4.21, §10.5, §10.11, §11.12. Implementation Notes §1 and §4 (sfp-observability).

### Affected Components
sfp-observability, sfp-messaging, all services, Infrastructure.

---

## ID-051

### Title
Use httpx (async) with tenacity for outbound HTTP.

### Status
Accepted

### Context
Services make outbound HTTP calls through internal provider adapters (Git Provider Adapter for GitHub, Slack provider abstraction, read-only query APIs, telemetry export). No HTTP client library is chosen. Services are async (ID-003), and telemetry must be automatic (ID-050).

### Decision
Use `httpx` in async mode as the outbound HTTP client. Layer `tenacity` on top for explicit, testable retry policies (exponential backoff with jitter) for provider calls subject to rate limits and transient failures. Standardize connection pooling and timeouts in the shared framework; provider adapters wrap httpx rather than calling providers directly. Outbound HTTP calls are logged with `correlation_id` via `sfp-observability`; OpenTelemetry instrumentation of httpx is deferred together with the rest of OTel (ID-050).

### Rationale
httpx provides native async, first-class timeouts and connection pooling, and pairs with FastAPI/Starlette. tenacity keeps retry behavior explicit and testable rather than hidden, which matters for GitHub/Slack rate limiting. Wrapping httpx in provider adapters preserves AP-007 vendor independence. OTel httpx instrumentation is deferred per ID-050; correlation-bound logging covers v0.

### Alternatives Considered
- aiohttp: rejected; dual-client surface with no v0 advantage.
- requests (sync): rejected for async services.
- Hand-rolled retries: rejected in favor of tenacity.

### Consequences
Positive: one async HTTP client, explicit retries, correlation-bound logging.  
Negative: retry policies and timeouts must be defined per provider adapter; automatic tracing of outbound calls is unavailable until OTel is added (ID-050).

### References
ID-003, ID-007, ID-035, ID-050, ID-048. Master Architecture Specification §9 (Git Provider Adapter, provider abstractions), AP-007.

### Affected Components
Shared Framework, Git Provider Adapter, Communication Service, External Events Service, Orchestrator, Workspace Worker.

---

## ID-052

### Title
Use no DI framework: manual composition root with FastAPI Depends.

### Status
Accepted

### Context
FastAPI `Depends` covers only the HTTP/query path. Services also have message consumers, workers, and entrypoints that must wire repositories, the Message Bus, configuration, and secrets. Implementation Notes §3 places DI container configuration in `entrypoints/`. A dependency-injection approach for non-HTTP paths is required.

### Decision
Do not introduce a DI framework. Use an explicit, manual composition root:
- FastAPI `Depends` continues to own the HTTP/query path.
- `entrypoints/` constructs and wires all dependencies (repositories, Message Bus, configuration, secrets, provider adapters) via plain Python, defining lifetimes explicitly (singleton infra clients such as the DB session factory and Message Bus; transient per-message units of work).
- For the message-consumer path, the messaging framework constructs handlers and injects dependencies (`MessageBus`, `MessageContext`) through a small explicit handler factory/provider, not a reflection-based container.

### Rationale
For v0 service sizes, a manual composition root is the simplest, most readable, and most debuggable option. It avoids framework magic and a learning curve while fully supporting the required lifetimes. Keeping wiring explicit in `entrypoints/` matches the uniform service layout (Implementation Notes §3) and AP-010 (internal abstractions owned by the platform).

### Alternatives Considered
- `dependency-injector` / `lagom` / `punq` / `injector`: rejected for v0; add magic and a DSL without sufficient benefit at this scale.
- FastAPI `Depends` everywhere: rejected; it does not cover workers and message consumers.

### Consequences
Positive: transparent wiring, no magic, easy to test.  
Negative: composition-root code is hand-maintained as services grow.

### References
ID-003, ID-048, ID-049. Implementation Notes §1 (MessageContext injection) and §3 (entrypoints/). Master Architecture Specification §10.7, §10.10, AP-010.

### Affected Components
Shared Framework (sfp-messaging), all services, entrypoints/.

---

## ID-053

### Title
Implement the Transactional Outbox as a Postgres outbox table with a SKIP LOCKED relay publisher.

### Status
Accepted

### Context
MAS §4.11 and §7.3 mandate a service-owned Transactional Outbox: business state, idempotency-ledger update, and outbound message intent must be persisted atomically; messages are published after commit; pending entries are retried. The concrete implementation pattern is required. MAS §11.5 requires a message to be acknowledged only after business state, idempotency records, and outbox intent are durably recorded.

### Decision
Implement the Transactional Outbox per service as follows:
- Each service owns an outbox table in its own Postgres database (alongside its business tables). Columns include an id, aggregate root reference, message_type, JSON payload (ID-013), idempotency_key, created_at, and a nullable published_at.
- The outbox row is written in the same database transaction as the business-state change and the idempotency-ledger update, guaranteeing atomicity (MAS §4.11).
- A service-owned relay publisher claims unpublished rows using `SELECT ... FOR UPDATE SKIP LOCKED`, publishes them to the Message Bus, and marks `published_at`. `SKIP LOCKED` allows multiple relay instances to run concurrently without double-claiming, supporting horizontal scale (MAS §10.13).
- Publishing is at-least-once: a crash between publish and marking can cause re-publish; consumers deduplicate via their idempotency ledger (ID-011), and outbox entries are idempotent as required by MAS §4.11.
- A consumer writes business state + idempotency + outbox row in one transaction, then acknowledges the inbound message; the relay publishes asynchronously afterward (MAS §11.5).

### Rationale
A Postgres outbox table plus a Python relay publisher is the simplest implementation that satisfies the MAS atomicity, retry, and idempotency requirements without additional infrastructure. `FOR UPDATE SKIP LOCKED` provides safe concurrent publishing. This keeps the stack AWS-first and ECS-simple (ID-015) and consistent with MAS §0.7 (simplicity over cleverness) and ID-009 (SNS/SQS, no Kafka).

### Alternatives Considered
- CDC/Debezium outbox: rejected for v0. It tails the Postgres WAL and is efficient at scale, but its native target is Kafka, which would introduce Kafka + Kafka Connect into a deliberately SNS/SQS-based, simplicity-first stack (ID-009). CDC is recorded as a future evolution point if throughput demands it and Kafka is already present.
- AWS DMS CDC / bespoke logical-decoding plugin: rejected; not simpler than the relay.

### Consequences
Positive: simple, observable, SNS/SSQ-native, horizontally scalable, no new broker.  
Negative: relay polling logic and outbox retention/cleanup policy must be implemented and operated per service.

### References
ID-005, ID-006, ID-009, ID-011, ID-013, ID-015. Master Architecture Specification §4.9, §4.10, §4.11, §7.3, §11.4, §11.5.

### Affected Components
sfp-messaging, all message-producing services, Persistence Layer, Infrastructure.

---

## ID-054

### Title
Use LocalStack to emulate SNS/SQS/DLQ and Secrets Manager locally, provisioned via Pulumi.

### Status
Accepted

### Context
Implementation Notes §5 references LocalStack for local development. The AWS surface SFP depends on includes SNS, SQS, DLQs (ID-009, ID-012) and Secrets Manager (ID-016). The exact LocalStack scope and wiring are required.

### Decision
Use LocalStack locally to emulate the AWS messaging surface only:
- Emulate SNS and SQS (with DLQs and redrive). These are in the LocalStack Community (free) tier, with SQS offering full API coverage including DLQ/redrive.
- Wire services to LocalStack via AWS endpoint resolution (e.g. `AWS_ENDPOINT_URL=http://localhost:4566`) so the real Message Bus code path runs unchanged locally.
- Provision local AWS resources by applying the same Pulumi program (ID-014) against the LocalStack endpoint, so local infrastructure definitions match production.
- Secrets Manager is intentionally NOT emulated by LocalStack. Secret resolution goes through the `sfp-config`/`sfp-auth` abstraction, which uses a local implementation (environment variables / a local secrets file) in local development and AWS Secrets Manager in production (ID-016). Local dev therefore never depends on LocalStack Secrets Manager, regardless of its tier.
- Scope: LocalStack does not emulate ECS (services run as local processes) and does not host Postgres (a container — see ID-055). Logging in local development goes to console (ID-050).
- The pure in-memory Message Bus fake from `sfp-testing` remains available for unit tests (Implementation Notes §1, §4); LocalStack is for local run and integration tests.

### Rationale
A messaging-only LocalStack scope (Community, free) lets the real Message Bus run locally without AWS credentials or a separate local message bus, while reusing the Pulumi definitions keeps local and production infrastructure consistent. Routing secrets through the `sfp-config` abstraction with a local provider removes any dependency on LocalStack Secrets Manager (and any associated Pro-tier cost) and matches ID-016 (code never reads encrypted config directly; the abstraction handles local-vs-prod). Excluding ECS and Postgres from LocalStack avoids emulating things better run directly.

### Alternatives Considered
- Emulate Secrets Manager in LocalStack: rejected; secret resolution is abstracted, so a local provider suffices and avoids an unverified/possibly-Pro dependency.
- Emulate everything in LocalStack (including ECS/Postgres): rejected; Postgres is better as a real container and services as local processes.
- Bootstrap script instead of Pulumi-to-LocalStack: rejected; reusing Pulumi keeps definitions identical to production.
- Separate in-memory bus for integration tests: rejected in favor of running the real Message Bus against LocalStack.

### Consequences
Positive: realistic local/integration messaging environment; one Pulumi definition for local and prod; no LocalStack Pro dependency.  
Negative: the local secrets provider must be implemented behind the `sfp-config` abstraction.

### References
ID-009, ID-012, ID-014, ID-016, ID-050, ID-055. Implementation Notes §1, §4, §5. Master Architecture Specification §10.5, §10.6.

### Affected Components
sfp-messaging, sfp-config (secrets), Infrastructure (local/), all services, CI/CD (integration tests).

---

## ID-055

### Title
Use Docker Compose for local dependencies only; run services on the host via uv.

### Status
Accepted

### Context
Implementation Notes §5 references Docker Compose for local development. A concrete local topology is required, consistent with Postgres persistence (ID-005), LocalStack (ID-054), OTel-based observability (ID-050), Alembic migrations (ID-008), the `uv` workspace (ID-048), and MAS §10.14 (one cluster, multiple logical databases).

### Decision
Define a `compose.yaml` in `infrastructure/local/` that brings up dependencies only:
- A Postgres container hosting multiple logical databases (Identity, Orchestrator, Communication, External Events) created at startup, matching MAS §10.14.
- A LocalStack container emulating SNS/SQS/DLQ and Secrets Manager (ID-054).
- An OpenTelemetry Collector container as the dev telemetry sink (ID-050).
- An init/one-shot step that applies Pulumi against LocalStack (ID-054) and runs Alembic migrations (ID-008) against each logical database, so `docker compose up` yields a fully provisioned local environment.
Services are run on the host with `uv run` (one process per service), not containerized in local development, for fast reload and debugging.

### Rationale
Separating concerns — Compose owns stateful and infrastructure dependencies, `uv` owns application processes — gives a fully provisioned local environment while keeping the fast `uv`-based iteration loop. One Postgres instance with logical databases matches MAS §10.14 without shared business ownership.

### Alternatives Considered
- Containerize services in Compose too: rejected for local dev; slower to iterate and duplicates the `uv` workflow.
- Separate Postgres instance per service: rejected; MAS §10.14 permits one cluster with logical databases.
- Manual dependency setup: rejected; Compose is reproducible.

### Consequences
Positive: one-command provisioned local environment; fast host-based service iteration.  
Negative: developers run services via `uv` alongside Compose; the init/provisioning step must stay robust.

### References
ID-005, ID-008, ID-048, ID-050, ID-054. Implementation Notes §5. Master Architecture Specification §10.5, §10.6, §10.14.

### Affected Components
Infrastructure (local/), all services, CI/CD (integration tests).

---

## ID-056

### Title
Use per-artifact SemVer with independent service releases via Git tags.

### Status
Accepted

### Context
Release and versioning strategy is unspecified. Distinct artifacts have different versioning needs: shared contracts (MAS §5.7/§4.18), shared packages, and independently deployable services (AP-002). The stack uses a monorepo with a `uv` workspace (ID-002, ID-048), GitHub Actions (ID-017), ECS (ID-015), and Pulumi (ID-014).

### Decision
Version artifacts independently with SemVer; there is no platform-wide release monolith:
- Shared contracts (`sfp-contracts`) carry explicit SemVer. Even though consumed as a workspace path dependency in v0 (ID-048), they are versioned so breaking changes are visible; a breaking contract change is a major bump and a new contract version per MAS §5.7.
- Shared packages (`sfp-messaging`, `sfp-observability`, etc.) carry SemVer, bumped as they evolve.
- Each service is versioned independently via directory-prefixed Git tags (e.g. `orchestrator/v0.1.0`, `identity-service/v0.1.0`), reflecting independent deployability (AP-002).
- Release mechanics: a Git tag triggers GitHub Actions to build and deploy that service to ECS (ID-017, ID-015); Pulumi applies the per-service infrastructure (ID-014).
- Each service and package maintains a `CHANGELOG.md` (Keep a Changelog format).
- The Master Architecture Specification version is separate documentation SemVer (MAS §0.4) and is not coupled to service or package versions.

### Rationale
Per-artifact SemVer matches the architecture: contracts evolve under §5.7 rules, shared packages evolve independently, and services are independently deployable (AP-002). Directory-prefixed tags make independent service release history explicit in Git. Tag-triggered CI keeps releases deterministic and traceable.

### Alternatives Considered
- Single platform-wide version (everything bumps together): rejected; contradicts independent service deployability (AP-002).
- Unversioned shared contracts in v0: rejected; contracts must reflect breaking changes per MAS §5.7.
- External release orchestration tooling: not selected for v0.

### Consequences
Positive: independent, traceable releases; contract evolution is visible.  
Negative: multiple version streams to track; changelog and tagging discipline required.

### References
ID-002, ID-014, ID-015, ID-017, ID-048. Master Architecture Specification §0.4, §4.18, §5.7, AP-002.

### Affected Components
Shared Contracts, Shared Packages, all services, CI/CD, Infrastructure.

---

## ID-057

### Title
Minimal v0 authorization: ProjectUser membership is the only access primitive; RBAC/ABAC deferred.

### Status
Accepted

### Context
The MAS defines authentication boundaries (External Events Service authenticates webhook providers, ID-029; Identity Service owns user identity, MAS §9.3) and service-level isolation (AP-001), but deliberately leaves authorization unowned: the Identity Service explicitly does not own permissions or authorization policy (MAS §9.3, §7.4). An authorization model for v0 is required to avoid silent ambiguity.

### Decision
Adopt a minimal, documented v0 authorization model:
- `ProjectUser` membership (MAS §6.3, owned by the Orchestrator) is the sole access primitive. A user who is a member of a project may act on that project's workflow (for example, approve, comment, request changes) within v0 scope.
- There are no roles, no fine-grained permissions, and no RBAC/ABAC in v0.
- Administrative and bootstrap actions (creating projects, managing endpoint configuration, managing external identity mappings) are performed out-of-band for v0.
- A richer RBAC/ABAC model is explicitly deferred as a future architectural revision. Such a revision must assign a real owner (identity ownership stays in the Identity Service; project-membership policy stays in the Orchestrator) and add the corresponding aggregates, policy evaluation points, and tests.

### Rationale
A membership-based model fits the existing `ProjectUser` aggregate and the intentionally narrow v0 scope (MAS §12.2), adds no new architecture, and removes silent ambiguity. Keeping RBAC/ABAC as a documented future revision preserves the MAS principle that architectural ownership is made explicit before a capability is considered part of the platform.

### Alternatives Considered
- Full RBAC/ABAC in v0: rejected; it is new architecture with no owner in the MAS.
- Defer authorization entirely / leave as an open question: rejected; it reintroduces the ambiguity this work is resolving.

### Consequences
Positive: unambiguous, minimal v0 access control using existing aggregates; no architectural addition.  
Negative: v0 has no roles or fine-grained permissions; administrative actions require out-of-band handling; RBAC/ABAC remains a known future revision.

### References
MAS §6.3, §7.4, §9.3, §9.5, §12.2, §12.4. ID-029.

### Affected Components
Orchestrator (ProjectUser), Identity Service, Communication Service (user interactions), Shared Contracts.

---

## ID-058

### Title
Organize each service PostgreSQL database with business and operational schemas; plural snake_case tables.

### Status
Accepted

### Context
Each service owns a PostgreSQL logical database (MAS §10.14, ID-055). The internal schema organization, naming conventions, where ORM tables live, and where the outbox and idempotency ledger reside (ID-053, ID-011) are required. SQLAlchemy is the ORM (ID-006) with persistence models kept separate from contracts and domain (ID-007), and Alembic owns migrations (ID-008).

### Decision
Adopt a uniform per-service schema organization:
- One logical database per service (e.g. `sfp_orchestrator`, `sfp_identity`, `sfp_communication`, `sfp_external_events`). The Workspace Worker has no business database.
- Within each service database, use named PostgreSQL schemas to separate concerns:
  - `business` for business-state tables, the ORM projection of the service's aggregates (e.g. Orchestrator: `projects`, `project_users`, `tickets`, `pr_specifications`, `coding_jobs`, `reviews`, `merges`, `deployments`, `user_decisions`, `workflow_decisions`).
  - `operational` for the Transactional Outbox table (ID-053) and the message/idempotency ledger (ID-011).
  - WorkflowDecision resides in `business` because it is a business fact (AP-005); no separate `audit` schema.
- Naming conventions: plural snake_case table names matching aggregates; singular aggregate class names in code; snake_case columns; timestamps suffixed `_at`; immutable identifiers as `<entity>_id`.
- ORM table classes live in `infrastructure/persistence/` per service, not in `domain/`, preserving the contract/domain/persistence separation (ID-007).
- Alembic owns all schema changes per service database (ID-008); migrations are per-service.
- No cross-service foreign keys (AP-001, MAS §7.9); references to other services' entities are stored as plain identifier columns.

### Rationale
Named schemas cleanly separate authoritative business state from operational plumbing, are uniform across services, and keep the outbox and idempotency ledger distinct from business facts. Plural snake_case tables and the `infrastructure/persistence/` location match the uniform service layout and the contract/domain/persistence separation. No cross-service foreign keys preserve ownership boundaries.

### Alternatives Considered
- Everything in the `public` schema: rejected; mixes business and operational state.
- A separate `audit` schema for WorkflowDecision: rejected; WorkflowDecision is a business fact.
- Cross-service foreign keys: rejected; violates AP-001 and MAS §7.9.

### Consequences
Positive: uniform, ownership-respecting, clean separation of business and operational state.  
Negative: per-service Alembic discipline and schema conventions must be maintained.

### References
ID-005, ID-006, ID-007, ID-008, ID-011, ID-053, ID-055. Implementation Notes §3. Master Architecture Specification §7.9, §10.14, AP-001, AP-005.

### Affected Components
Persistence Layer, all services (except Workspace Worker business persistence), Infrastructure.

---

## ID-059

### Title
Organize agent prompts under services/workspace-worker/prompts, composed at runtime via a PromptBuilder.

### Status
Accepted

### Context
The Workspace Worker hosts the Coding Agent and Review Agent (MAS §9.6; ID-022, ID-023) on the Claude Agent SDK (ID-018). Where agent prompts and instructions live, how they are versioned, and how they are loaded at runtime are required. The platform is deterministic (AP-011) and owns its abstractions (AP-010).

### Decision
Keep agent prompts as Workspace-Worker-owned execution assets:
- Location: `services/workspace-worker/prompts/`, structured as `coder/` (system prompt, role, coding guidelines, test requirements), `reviewer/` (system prompt, review rubric covering requirements/tests/quality/security/edge cases per ID-023, comment resolution policy per ID-040), and `shared/` (cross-cutting fragments such as repository conventions, output format, and the deterministic JSON contract from ID-021 where relevant).
- Format: Markdown fragments composed at runtime. The system prompt is assembled from the shared base plus the role-specific and task-specific sections.
- Loading: prompts are read through a `PromptBuilder` abstraction owned by the Agent Runtime (AP-010); prompt text is never hardcoded inline in agent code.
- Versioning: prompts are treated as source. They are Git-versioned, reviewed in CI (ID-017), and released with the Workspace Worker (ID-056).
- Prompts are execution details and never belong to shared contracts (MAS §5).

### Rationale
Co-locating prompts with the Workspace Worker matches Agent Runtime ownership (MAS §9.6) and keeps them out of shared contracts. Markdown fragments composed at runtime keep prompts reviewable, diffable, deterministic (AP-011), and testable, while a PromptBuilder preserves the platform-owned abstraction over the Claude Agent SDK (AP-010, ID-018).

### Alternatives Considered
- Prompts in a shared package: rejected; only the Workspace Worker uses them, and they are execution details, not platform contracts.
- Hardcoded inline prompt strings: rejected; not reviewable, not deterministic-friendly, and not abstracted.
- Single monolithic prompt file: rejected in favor of composable fragments.

### Consequences
Positive: reviewable, versioned, deterministic prompts behind an owned abstraction.  
Negative: prompt composition and the PromptBuilder must be maintained and tested.

### References
ID-018, ID-021, ID-022, ID-023, ID-040, ID-056, ID-017. Master Architecture Specification §5, §9.6, AP-010, AP-011. Implementation Notes §3.

### Affected Components
Workspace Worker, Agent Runtime, CI/CD.

---

## ID-060

### Title
Isolate agent-generated execution per CodingJob in a sandboxed container on ECS.

### Status
Accepted

### Context
The Workspace Worker runs agent-generated code: the Coder writes code and runs build/tests/lint (MAS §9.6 Local Execution Engine; ID-022), the Reviewer runs checks (ID-023), and the Agent Runtime performs tool execution (MAS §9.6). Running arbitrary AI-generated code locally is a security and isolation concern. The stack uses ECS (ID-015), ephemeral worktrees (ID-033), and the Git Provider Adapter for outbound source control (ID-035).

### Decision
Isolate execution per CodingJob using the task-per-job execution model (ID-061, Model A1), realized on AWS Batch on Fargate (ID-015): each CodingJob runs as its own ephemeral Batch task, and that task is the sandboxed container. There is no separate orchestrator launching child containers, and no custom dispatcher (Batch provides the job queue, per-job containers, scale-to-zero, and retries).
- Each CodingJob executes as an ephemeral AWS Batch task on Fargate (one task = one sandboxed container). Generated code never runs directly on a shared task or host filesystem.
- Sandbox boundaries: a restricted filesystem (only the worktree is mounted writable; the rest is read-only), no network egress by default for build/test runs, with network access limited to provider calls routed through the Git Provider Adapter (ID-035) under explicit allow-listing; all Linux capabilities dropped; no privileged mode; resource limits (CPU, memory, time) per job; and a non-root user.
- Stronger isolation (gVisor/Firecracker) is deferred to a future hardening pass.
- Ephemeral and clean: the task/container and worktree are torn down after the job (ID-033); no state persists and there is no cross-job leakage.
- Dev parity: locally, build/test also runs inside a container so development matches production isolation rather than executing generated code on the host.

### Rationale
Making the Workspace Worker instance itself the per-job sandboxed container (Model A1) gives the strongest, simplest isolation with no extra orchestration layer: each job gets a fresh, least-privilege container that is fully torn down afterwards. Default-off network egress, dropped capabilities, resource limits, and ephemeral teardown prevent leakage between jobs. Dev parity avoids a false sense of safety during local iteration.

### Alternatives Considered
- Direct execution on the ECS task/host filesystem: rejected; unsafe for untrusted generated code.
- Default network egress: rejected; violates least-privilege.
- Long-running orchestrator task spawning child sandbox containers (Model B): rejected for v0; adds an orchestration layer without benefit given heavy jobs and the per-job teardown requirement.
- gVisor/Firecracker isolation now: deferred to a hardening pass as heavier than v0 needs.

### Consequences
Positive: bounded, least-privilege, leak-free execution of generated code with no extra orchestration layer and no custom dispatcher (Batch provides dispatch, scale-to-zero, retries).  
Negative: per-task container startup and egress allow-listing must be implemented; the SQS→Batch bridge that submits one Batch task per dequeued command must be wired (ID-061).

### References
ID-015, ID-022, ID-023, ID-033, ID-035, ID-055, ID-061. Master Architecture Specification §9.6, §10.5.

### Affected Components
Workspace Worker, Repository Manager, Local Execution Engine, Git Provider Adapter, Infrastructure.

---

## ID-061

### Title
Workspace Worker execution model: one ephemeral instance per job (Model A1) with backlog-driven scaling and a max-Workers ceiling.

### Status
Accepted

### Context
MAS §11.10/§11.15 state that execution capacity scales via Scheduler policies plus the number of Workspace Workers plus queue depth, and ID-044 makes SQS queue depth the back-pressure signal. Concrete worker concurrency and cluster scaling thresholds are required. MAS §9.6 frames Workspace Worker instances as equivalent units that each execute commands and compete for them. CodingJobs are non-interactive (ID-032) and heavy (agent runtime + build + tests), and each runs in its own sandboxed container (ID-060).

### Decision
Adopt the task-per-job execution model (Model A1), realized on AWS Batch on Fargate (ID-015):
- Each Workspace Worker command (ExecuteCodingJob, ReviewPullRequest, SynchronizePullRequest, RequestMerge, CancelCodingJob, CancelReviewJob) runs as its own ephemeral Batch task on Fargate: one task = one sandboxed container = one job at a time. A small SQS→Batch bridge (e.g. a Lambda) submits one Batch task per dequeued command, preserving the Message Bus contract (MAS §4).
- Per-task concurrency is 1 by construction (not a tunable threshold). Total platform concurrency = the number of running Batch tasks.
- Scheduler admission (MAS §11.8): the Orchestrator's Scheduler admits execution-bound commands up to available Worker capacity; Batch's compute environment enforces the concurrency cap. Communication commands (NotifyUser, RequestUserInput) are emitted immediately, as in MAS §11.8.
- Scaling: Batch scales the Fargate compute environment up and down (including to zero) natively based on job-queue depth, so no custom CloudWatch scaling policy or dispatcher is required. SQS queue depth remains the back-pressure signal upstream of the bridge (ID-044).
- Concurrency ceiling: enforce a configurable maximum via the Batch compute environment max-vCpus (default equivalent to 5 concurrent jobs for v0). This caps both compute and concurrent LLM token spend, since each running job burns tokens.
- These thresholds are implementation-level and tunable without architectural revision (MAS §12.4).

### Rationale
Model A1 (one ephemeral Batch task per job) matches MAS §9.6 (instances each execute commands and compete for them), gives the strongest per-job isolation by making the task the sandbox (ID-060), keeps capacity reasoning simple (1 task = 1 job), and scales to zero when idle so compute cost is pay-per-use for v0's intermittent load. Batch provides dispatch and scale-to-zero natively, removing the need for a custom dispatcher or custom scaling policy. The max-vCpus ceiling bounds the dominant variable cost (LLM tokens), not just compute.

### Alternatives Considered
- Long-running Worker pool consuming one job at a time (Model A2): simpler operationally but weaker per-job isolation and a nonzero idle baseline cost.
- Long-running orchestrator spawning multiple concurrent child containers (Model B): rejected; CodingJobs are heavy, so multiplexing yields little saving while adding an orchestration layer.
- Fixed worker concurrency greater than 1: rejected; conflicts with per-job container isolation (ID-060) and complicates capacity reasoning.

### Consequences
Positive: simple capacity model, strongest isolation, native scale-to-zero (Batch), explicit token/compute ceiling; no custom dispatcher or scaling policy required.  
Negative: the SQS→Batch bridge must be wired; per-task cold-start (acceptable for minutes-long jobs).  
Note: with Batch providing native scaling, the custom CloudWatch backlog-per-task target-tracking pattern is not required for the Workspace Worker pool; it remains a documented, viable fallback if a pure-ECS model is ever adopted (AWS Application Auto Scaling, `ApproximateNumberOfMessagesVisible / RunningTaskCount`).

### References
ID-015, ID-032, ID-033, ID-044, ID-060. Master Architecture Specification §9.6, §11.8, §11.10, §11.15, §12.4.

### Affected Components
Workspace Worker, Scheduler (Orchestrator), Infrastructure (ECS), Observability.

---

## ID-062

### Title
CI/CD matrix on GitHub Actions: lint, type-check, test, release, and infrastructure workflows.

### Status
Accepted

### Context
GitHub Actions is the CI/CD tool (ID-017) for a `uv` monorepo workspace (ID-048) with uniform service layout (Implementation Notes §3), per-artifact SemVer released via directory-prefixed Git tags (ID-056), a 90% coverage floor (ID-049), Pulumi IaC (ID-014), ECS deployment (ID-015), and LocalStack + Postgres for integration tests (ID-054, ID-055). The concrete workflow matrix is required. MAS §10.12 states deployment workflows are infrastructure.

### Decision
Define a layered GitHub Actions matrix using reusable workflows (`workflow_call`) so every service and shared package calls the same templates, enforcing the uniform layout:
- `ci` workflow (on every PR and on push to main):
  - Lint and format check with `ruff` (`ruff check`, `ruff format --check`).
  - Static type check with `mypy`.
  - Install with `uv sync --frozen` to enforce the lockfile (ID-048).
  - Tests with `pytest --cov --cov-fail-under=90` (ID-049).
  - Path-filtered per-project execution on PRs (run only the touched service/package), with a full workspace run on main.
  - Integration tests for services that need them, using LocalStack and Postgres (ID-054, ID-055).
  - Secret scanning (GitHub native plus dependency/lockfile audit).
- `release` workflow (on a directory-prefixed tag, e.g. `orchestrator/v0.1.0`, per ID-056): build the service image, push to ECR, apply Pulumi for that service (ID-014), and deploy to ECS (ID-015). Tag-driven and per-service; no monolith release.
- `infrastructure` workflow (on changes under `infrastructure/`): `pulumi preview` on PR and `pulumi up` on merge to main, for platform and per-service stacks.
- Reusable workflow templates are shared so all services and packages follow identical ci and release steps.

### Rationale
A layered, reusable-workflow matrix enforces uniformity across the monorepo (Implementation Notes §3) while keeping PR feedback fast via path filtering. Folding lint (`ruff`), typing (`mypy`), the frozen lockfile, and the 90% coverage gate into CI makes every gate deterministic and agent-friendly. Tag-driven, per-service release matches independent deployability (AP-002) and ID-056, and the infrastructure workflow keeps Pulumi changes reviewed before apply.

### Alternatives Considered
- A single monolithic workflow for everything: rejected; slow and contradicts per-service independence.
- No static type checking: rejected; `mypy` raises the quality bar alongside the 90% coverage floor.
- Per-service bespoke pipelines: rejected in favor of reusable workflows for uniformity.
- Skip path filtering: rejected; full runs on every PR are slow at monorepo scale.

### Consequences
Positive: deterministic, uniform CI/CD; fast PR feedback; tag-driven per-service releases; infra changes reviewed before apply.  
Negative: reusable workflows and path filters must be maintained; LocalStack-based integration tests add CI runtime.

### References
ID-014, ID-015, ID-017, ID-048, ID-049, ID-054, ID-055, ID-056. Implementation Notes §3. Master Architecture Specification §10.6, §10.12, AP-002.

### Affected Components
CI/CD, all services, Shared Packages, Infrastructure, Pulumi.

---

## ID-063

### Title
Per-role configurable agent models; v0 defaults to a strong coding model; downgrades are gated on a measured experiment.

### Status
Accepted

### Context
The Planner produces deterministic PRSpecifications (ID-021), which front-loads "what to build." A reasonable hypothesis is that the Coder could then run on a cheaper, lower-reasoning model. However, even with a good specification, the Coder must still navigate an unfamiliar repository, match conventions, integrate with existing code, and—critically—write tests and debug failures (ID-022, ID-039), which is the most reasoning-intensive part of the job and where weaker models fail. Most rework iterations arise from integration and debugging, not from specification ambiguity, so a cheaper coder can raise the rework count R (ID-061) enough to erase token savings and reduce quality. The Agent Runtime is model-independent (MAS §9.6, AP-007) and provider/model selection is configuration-driven (ID-019, ID-020), so per-role model selection requires no architecture change.

### Decision
Select the model per agent role, configured through the Agent Runtime (Planner, Coder, Reviewer each have their own model setting):
- Storage (extends ID-020, ID-016; MAS §10.7): model configuration is injected, never hardcoded. Shared endpoint/auth come from `ANTHROPIC_BASE_URL` and `ANTHROPIC_AUTH_TOKEN` (or `ANTHROPIC_API_KEY`). Per-role models come from environment variables `SFP_AGENT_MODEL_PLANNER`, `SFP_AGENT_MODEL_CODER`, `SFP_AGENT_MODEL_REVIEWER` (with sensible defaults if unset). These are loaded by `sfp-config` into a typed `AgentModelConfig` and injected into the Agent Runtime via the composition root (ID-052). In ECS/Batch they are supplied through the task environment / Secrets Manager / AppConfig, never baked into the image. The service validates model configuration on startup and fails fast if a role has no resolvable model.
- v0 default: all three roles (Planner, Coder, Reviewer) start on a strong, frontier-class coding model.
- The Reviewer remains on a strong model regardless of Coder tier, because it is the quality gate (ID-023); a weak Coder plus a weak Reviewer is the failure mode to avoid.
- Downgrading the Coder (or Planner) to a cheaper model is permitted only after a measured experiment proves it is safe. The experiment runs the candidate model on a sample of CodingJobs and measures: first-pass approval rate, the resulting rework count R, and whether the Reviewer's defect-catch rate holds.
- The cost tradeoff must be validated, not assumed: a cheaper model only saves money if `cheaper_token × (1 + R_cheap)` is below `expensive_token × (1 + R_strong)`. If R rises enough, the cheaper model costs more and ships worse code, and the downgrade is rejected.

This refines provider/model selection (ID-018, ID-019) and extends the empirical validation gate (B2): provider choice and per-role model tier are both configuration-driven and both settled by measurement, not assumption.

### Rationale
Per-role model selection captures the legitimate leverage of deterministic planning without assuming a cheap Coder is sufficient. Starting strong and downgrading only on measured evidence avoids the trap where a lower-reasoning Coder raises R and increases total token cost while lowering quality. Keeping the Reviewer strong preserves the automated quality gate. The approach is consistent with the model-independent Agent Runtime and config-driven routing already chosen.

### Alternatives Considered
- Default the Coder to a basic/cheap model in v0 because the Planner is deterministic: rejected as an assumption; debugging and integration are reasoning-intensive, and higher R can erase savings.
- One model for all roles: rejected; roles have different demands and per-role selection is configuration-only.
- Allow unvalidated downgrades: rejected; cost and quality impact must be measured via R and Reviewer catch-rate.

### Consequences
Positive: cost-optimal model tiering without quality risk; empirical, reversible downgrades; Reviewer stays a strong gate.  
Negative: a measured downgrade experiment is required before any cost saving is realized; model-tier configuration and experiment harness must be built.

### References
ID-018, ID-019, ID-020, ID-021, ID-022, ID-023, ID-039, ID-061. Master Architecture Specification §9.6, AP-007, AP-010.

### Affected Components
Workspace Worker, Agent Runtime, Configuration, Observability (R / approval metrics).

---

## ID-064

### Title
Ticket Readiness Gate: an Orchestrator use case that blocks ambiguous tickets before planning.

### Status
Accepted

### Context
v0 coding is non-interactive (ID-032): a ticket with unresolved ambiguities becomes a PRSpecification that a CodingJob cannot implement, so it fails with `INSUFFICIENT_SPECIFICATION`. To avoid wasting execution and to honor the "ticket is the contract" principle (determinism funnel), a ticket should be validated as ready before the Planner runs (ID-021). PRSpecification generation is an Orchestrator internal use case (MAS §5.3), and the Workspace Worker owns the agentic Agent Runtime (MAS §9.6).

### Decision
Add a Ticket Readiness Gate as an Orchestrator internal use case, run before the Planner:
- Placement: the readiness evaluator runs as a direct model call inside the Orchestrator (not via the Workspace Worker Agent Runtime). Readiness and planning are reasoning-over-text tasks that need no tool-use, repository access, or sandbox, so they are distinct from the Workspace Worker's agentic execution and belong with the Orchestrator's internal planning use cases (MAS §5.3).
- Two layers:
  1. A deterministic readiness rubric, rule-checked where possible, verifying the ticket enables the primitives the Planner outputs (ID-021): objective, scope, acceptance criteria, testing requirements, dependencies, affected areas, and explicit assumptions. A missing required field is an automatic Not-Ready with no model call.
  2. A model-based evaluator scoring the semantic dimensions: completeness, decomposability, testability, scope-boundedness, and unambiguity (the presence of any blocking decision a CodingJob would have to make at runtime).
- "Deterministic / pipeline-ready" is operationalized as zero blocking ambiguities: the gate returns Ready only when the evaluator finds no runtime decision left for a CodingJob. Otherwise it returns Not-Ready with the specific list of gaps, which the Orchestrator routes back to the user via the Communication Service (RequestUserInput) for clarification.
- Safety net and feedback: because the evaluator is probabilistic, the downstream fail-fast (ID-032, INSUFFICIENT_SPECIFICATION) remains the backstop. Every such downstream failure is a labeled example used to improve the gate, so readiness accuracy improves over time rather than being certified once.

### Rationale
Catching ambiguity before planning prevents wasted execution in a non-interactive v0 and strengthens the determinism funnel (the ticket is the contract). A rubric handles mechanically-checkable completeness; a model evaluator handles semantic ambiguity. Operationalizing determinism as zero blocking ambiguities is the most honest measurable bar. Placing the evaluator in the Orchestrator as a direct model call matches §5.3 and keeps the Workspace Worker's heavy Agent Runtime reserved for agentic execution.

### Alternatives Considered
- Run the evaluator through the Workspace Worker Agent Runtime: rejected; readiness needs no tool-use/repo/sandbox, and §5.3 makes planning an Orchestrator use case.
- No gate; rely only on downstream CodingJob failure (ID-032): rejected as the sole mechanism; it wastes execution and provides no pre-pipeline quality control.
- Claim a single "determinism percentage": rejected; determinism is a judgment, operationalized as zero-blocking-ambiguity plus a feedback loop, not a certified number.

### Consequences
Positive: ambiguous tickets are blocked before planning; wasted execution reduced; a measurable readiness bar with a self-improving feedback loop.  
Negative: the evaluator is probabilistic (false-ready / false-not-ready possible), so the downstream fail-fast backstop and feedback loop are required; the rubric and evaluator prompt must be maintained.

### References
ID-021, ID-032, ID-063, ID-024. Master Architecture Specification §5.3, §9.5, §9.6. Implementation Notes §3.

### Affected Components
Orchestrator (Ticket lifecycle, planning), Communication Service (clarification routing), sfp-config (evaluator model).

---

## ID-065

### Title
Tickets are AI-executable or Manual; Manual tickets are human prerequisites, not CodingJobs.

### Status
Accepted

### Context
Not every ticket can be executed by an AI agent (cloud account creation, domain registration, Firebase/Play Console setup, secret generation — these require human identity, billing, or console access). The ARCONTA blueprint marks this with 🤖/👤 + Jira labels (74 AI / 3 Manual; the 3 manual are all provisioning). The distinction must be reflected without overcomplicating the domain model.

### Decision
A Ticket is either AI-executable or Manual (in v0, derived from the ticket label):
- AI-executable tickets enter the production pipeline: PRSpecification → CodingJob → Review → Merge → Deploy.
- Manual tickets do NOT enter the pipeline. They are human prerequisites performed out-of-band: no CodingJob, no Review, no Pull Request. A CodingJob is always an AI implementation execution by the Workspace Worker; there is no `executor` attribute and no "manual CodingJob."
- A Manual ticket blocks its dependent AI tickets (via dependency links) until the human completes it. On completion it emits structured context outputs (ID-071) — e.g. account IDs, secret/ARN locations, endpoints — that dependent AI tickets consume.
- The Readiness Gate (ID-064) classifies a ticket as manual-required when no agent can execute it; the ticket is routed to the human and is not planned or coded. For a dependent AI ticket, the gate treats an incomplete manual dependency as "not ready: waiting on PRD-X (manual)."

### Rationale
Manual tickets are provisioning (accounts, secrets, domains), not implementation — they involve no code and therefore nothing to review. Modeling them as CodingJobs (with an executor attribute) would overload the CodingJob concept and add unneeded complexity. Treating them as blocking prerequisites that emit context outputs is simpler, matches reality, and preserves the dependency/context flow (ID-071).

### Alternatives Considered
- CodingJob with executor = AI | HUMAN and a "re-enter at review" manual path: rejected; overloads CodingJob and implies reviewing non-code work.
- Treat all tickets as AI-executable; manual ones fail: rejected; wastes nothing useful and provides no clean human path.
- Leave execution mode only in Jira labels, unrecorded: rejected; the source-of-truth docs must reflect it.

### Consequences
Positive: simple domain model (CodingJob is always AI); manual tickets are clean blocking prerequisites; context still flows to dependents (ID-071).  
Negative: dependent AI tickets must wait for manual prerequisites to be completed by a human (inherent — these are genuinely human tasks).

### References
ID-064, ID-071, ID-072. Master Architecture Specification §6.4 (dependencies), §6.9 (CodingJob).

### Affected Components
Orchestrator (Ticket lifecycle, Readiness Gate, dependency resolution), Communication Service (track/notify manual completion).

---

## ID-066

### Title
Agent outputs are strict JSON contracts; the Orchestrator decides only from structured fields.

### Status
Accepted

### Context
Adopted from the lean handoff. Agent (LLM) outputs are non-deterministic; workflow decisions must not depend on parsing natural language.

### Decision
Every agent (Planner, Coder, Reviewer, Test Designer, Readiness evaluator) returns output that validates against a strict JSON schema, defined as platform contracts in `sfp-contracts`. A common envelope is required:
`{schema_version, agent, ticket_id, timestamp, status (SUCCESS|FAILED|BLOCKED|NEEDS_HUMAN|NEEDS_RETRY), payload, human_readable_summary}`.
The Orchestrator makes workflow decisions only from structured fields; the `human_readable_summary` is for traceability and debugging only. Invalid/non-conformant agent output is rejected (treated as a failure/retry).

General rule: **contracts carry decision-relevant structured judgments and references, never artifacts or deterministic facts that already live in a source-of-truth system.** Concretely:
- Code is not in the coder-output (it lives on the GitHub branch/PR; the contract references it via `branch_name`, `pull_request_url`).
- Review comments are not in the reviewer-output (they live on GitHub as the PR review; the coder fetches them there for rework, exactly as it fetches code).
- Deterministic facts the Orchestrator already obtains from GitHub/gate results are not echoed in agent outputs.

Per-agent payload schemas:
- **planner-output** → `pr_specs[]` (id, title, goal, scope, out_of_scope, acceptance_criteria, dependencies, validation_profile, required_gates, likely_files_or_modules, implementation_notes).
- **coder-output** → `pr_spec_id, branch_name, pull_request_url, files_changed, tests_added_or_updated, validation_status, validation_evidence, known_limitations` (no code body).
- **reviewer-output** → `pr_spec_id, review_status (APPROVED|CHANGES_REQUESTED|BLOCKED|NEEDS_HUMAN_DECISION), quality_gates{blueprint_compliance, acceptance_criteria_satisfied, test_plan_satisfied, no_unrelated_changes, maintainability_acceptable, security_acceptable}`. The `quality_gates` are holistic, PR-scoped reviewer **judgments only** — `ci_passed` and `validation_profile_gates_satisfied` are omitted because the Orchestrator already has them from GitHub Actions and gate results. Review comments themselves are not included (they live on GitHub; see general rule).
- **test-designer-output** → `pr_spec_id, test_plan{unit_tests, integration_tests, e2e_or_smoke_tests, negative_tests, edge_cases, required_validation_commands}`.

### Rationale
Strict JSON contracts make agent outputs machine-consumable and deterministic, keep vendor/model differences behind the contract, and let the Orchestrator decide reliably. The "judgments + references, not artifacts" rule keeps a single source of truth (GitHub for code and review comments; GitHub Actions/gates for deterministic facts) and avoids dual-write divergence. This generalizes the deterministic PR-specification idea (ID-021) to every agent boundary.

### Alternatives Considered
- Free-form agent text parsed heuristically: rejected; non-deterministic and brittle.
- Per-agent ad-hoc formats: rejected in favor of a common envelope + typed payloads.
- Including review comments in reviewer-output: rejected; comments live on GitHub (the reviewer submits them as a PR review), and duplicating them risks divergence — same principle as not embedding code.
- Including ci_passed / validation gates in reviewer-output: rejected; these are deterministic facts the Orchestrator already has from GitHub/gate results.

### Consequences
Positive: deterministic workflow decisions; testable agent boundaries; model-independent contracts; single source of truth (no dual-write).  
Negative: schemas must be designed, versioned, and enforced; agent prompts must produce conformant output; the coder must fetch review comments from GitHub during rework.

### References
ID-021, ID-059, ID-063. Master Architecture Specification §5. Adopted from `software-factory-handoff/contracts/`.

### Affected Components
sfp-contracts, Orchestrator, Workspace Worker (Agent Runtime), all agents.

---

## ID-067

### Title
Validation Profiles (LEVEL_1–LEVEL_4) assign risk-tiered gate sets per PR specification.

### Status
Accepted

### Context
Adopted from the lean handoff. Not every change needs the same gates; running full E2E on a doc change is wasteful, while a migration needs extra verification.

### Decision
Every PRSpecification carries a validation profile assigned by the Planner:
- LEVEL_1_INTERNAL — no runtime/user impact (docs, tests-only, refactor, formatting): CI + unit tests if applicable. **No human approval required** — may auto-merge after the Reviewer and CI/unit tests pass.
- LEVEL_2_BACKEND_OR_API — backend/API/logic, no direct user workflow: CI, unit, integration when applicable, preview when deployable, smoke. **Human approval required.**
- LEVEL_3_USER_FACING — user-visible: CI, unit, integration when applicable, preview, E2E/smoke. **Human approval required.**
- LEVEL_4_HIGH_RISK — security, data, infra, billing, auth, irreversible: CI, unit, integration, preview/equivalent, E2E/smoke, post-deploy verification. **Human approval required.**
Rule: when in doubt, choose the higher level. The profile determines which gates the workflow enforces and whether human approval is required before merge (ID-024 is amended accordingly: LEVEL_1 is exempt from the human-approval requirement). The Reviewer always runs regardless of level; the profile adds extra gates and the human-approval requirement. ID-068 failure classification interacts with this.

### Rationale
Risk-tiered gate sets avoid both under- and over-validation, keep the workflow adaptive, and make the required quality bar explicit per change. Tiering human approval (everything except LEVEL_1) removes the human bottleneck for genuinely no-impact changes while preserving accountability for any change that can affect behavior. This is a workflow-policy elaboration of MAS §8 (no change to ownership or authority).

### Alternatives Considered
- One fixed gate set for all PRs: rejected; wasteful for low-risk, insufficient for high-risk.
- Let the coder choose gates: rejected; the Planner/Reviewer own gate selection.
- Human approval for all levels (including LEVEL_1): rejected as an unnecessary bottleneck for no-impact changes (see ID-024).
- No human approval for any level (fully autonomous merge): rejected for v0.

### Consequences
Positive: adaptive, explicit quality gates per change; LEVEL_1 changes are fully automated end-to-end.  
Negative: profile selection and gate-mapping must be defined and enforced; correct level classification is safety-critical (a mis-classified LEVEL_1 change could auto-merge).

### References
ID-021, ID-022, ID-023. Master Architecture Specification §8. Adopted from `software-factory-handoff/workflow/validation-profiles.md`.

### Affected Components
Orchestrator (Planner, workflow policies), Workspace Worker (Local Execution Engine), Reviewer.

---

## ID-068

### Title
Failure classification: normal development failures stay in the loop; BLOCKED means external intervention required.

### Status
Accepted

### Context
Adopted from the lean handoff. MAS §11.6 classifies failures but does not crisply separate "implementation-loop failures" from "factory-blocked" failures.

### Decision
Two categories stay out of BLOCKED, and must be distinguished from each other:
- **Normal rework (not a failure).** A `CHANGES_REQUESTED` review is the expected coder↔reviewer quality loop, not a failure. It is normal workflow progression: the Coder addresses the review comments and re-submits. It never escalates to BLOCKED.
- **Development failures** (transient, handled inside the implementation loop): lint, typecheck, build, unit/integration test failures, and CI failures during active implementation. The Coder fixes and re-pushes. These do NOT move the ticket to a blocked/terminal state.

A ticket is BLOCKED only when the factory cannot continue without external intervention: incomplete dependency, missing dependency context (ID-071), missing secret/credential, repository inaccessible, unresolved human clarification, unrecoverable merge-queue failure, deployment failure requiring human action, or external system unavailable.

Auto-recoverable issues are retried; human-recoverable issues go through the CONFIRM flow (ID-069); normal rework and development failures are handled by Coder/Reviewer without escalating to BLOCKED.

### Rationale
Crisp classification prevents operational noise and, importantly, stops treating the review loop as an error — rework is how the system is supposed to work. Genuinely stuck work still surfaces for human action. Elaborates MAS §11.6.

### Alternatives Considered
- Treat any failure or rework as blocked: rejected; creates noise, hides real blockers, and pathologizes the normal review loop.
- No blocked state: rejected; some situations genuinely need humans.

### Consequences
Positive: low-noise, correct escalation; the review loop is recognized as normal progress.  
Negative: classification rules must be implemented and tuned.

### References
ID-032, ID-069, ID-071. Master Architecture Specification §11.6. Adopted from `software-factory-handoff/workflow/blocking-policy.md`.

### Affected Components
Orchestrator (Workflow Engine, failure handling), Workspace Worker, Communication Service.

---

## ID-069

### Title
Human interactions end with a generated summary and explicit CONFIRM before being persisted.

### Status
Accepted

### Context
Adopted from the lean handoff. Human input captured from Slack must not be trusted as-is; it must be structured and confirmed before becoming durable business state.

### Decision
Every human interaction follows: Orchestrator identifies the need → sends Slack message → human converses → the Communication Agent generates a structured summary → the human must reply exactly `CONFIRM` → if corrected, the summary is regenerated and confirmation re-requested → only on CONFIRM is the summary persisted as a UserDecision (and, where relevant, as a `context_added` fact per ID-071). The CONFIRM gate applies to **all** interactions, including quick approvals — no exceptions for low-stakes decisions. Jira holds the human-facing record; SFP owns the structured UserDecision.
Allowed human decisions in v0: APPROVE, REQUEST_CHANGES, REJECT, PROVIDE_CONTEXT, ANSWER_QUESTION, CLARIFICATION. (`OVERRIDE` — bypassing an automated decision — is excluded from v0; it is a future escape hatch.)

### Rationale
The universal CONFIRM gate prevents mis-captured human intent from corrupting workflow state and produces a durable, structured, attributable decision — consistent with AP-005 (immutable business facts) and the Communication Service model (MAS §9.4). Applying it to every interaction keeps the guarantee uniform and avoids a class of edge cases around "which decisions need confirmation." Excluding OVERRIDE keeps v0 deterministic: humans participate through defined decisions, not by overriding the workflow.

### Alternatives Considered
- Persist raw Slack messages: rejected; AP-009 forbids persisting transcripts, and intent would be ambiguous.
- Persist without confirmation: rejected; risks mis-captured decisions.
- CONFIRM only for high-stakes decisions (one-step approvals): rejected; the user chose a uniform CONFIRM for all interactions.
- Include OVERRIDE in v0: rejected; it can bypass the workflow and undermine determinism; deferred.

### Consequences
Positive: durable, confirmed, structured human decisions; uniform guarantee; traceable; v0 stays deterministic.  
Negative: every interaction incurs a confirm round-trip; summary quality matters; no override escape hatch in v0.

### References
ID-024, ID-071. Master Architecture Specification §9.4, AP-005, AP-009. Adopted from `software-factory-handoff/workflow/human-interaction.md`.

### Affected Components
Communication Service (Communication Agent), Orchestrator (UserDecision), Identity Service.

---

## ID-070

### Title
The AI Implementation Specification is a deterministic ticket format (the ARCONTA blueprint template).

### Status
Accepted

### Context
The bootstrap hierarchy (fix #3) defines the Engineering Backlog ticket as the terminal, fully-deterministic artifact an AI executes. ARCONTA's blueprint tickets are a concrete, high-quality realization of that concept.

### Decision
Adopt the ARCONTA blueprint ticket structure as the canonical AI Implementation Specification template. An AI-executable ticket contains: header (ID, area, executor 🤖/👤, title); metadata (Type, Label/executor, Dependencies, Repo); Context; Requirements; Files to create/modify (explicit paths); Implementation notes; References (spec sections, related tickets); **Context outputs / required inputs** — a mandatory section declaring the ticket's outputs (name + type) and the dependency-produced inputs it needs (per ID-071); Acceptance criteria (checklist).
A Manual ticket additionally contains "Human action required" rationale, cost/notes, "What the human must do" steps, a Verification checklist, and its declared context outputs (the facts it produces for dependents, per ID-071).

Coverage expectation in the acceptance criteria: **≥90% coverage is the enforced CI gate (ID-049); 100% coverage is the aspiration for the logic implemented in the PR.** Both are stated in the checklist.

### Rationale
A fixed, deterministic ticket template makes every ticket self-contained and machine-executable — directly realizing "the ticket is the contract" and MAS §12.9 (a ticket must not require the assignee to make unresolved decisions). Making context outputs/inputs a mandatory section bakes the ID-071 cross-ticket data flow into every ticket, so dependencies are resolvable by the Readiness Gate. Aligning the coverage wording to "90% gate, 100% aspiration" keeps the template consistent with ID-049 without weakening the quality intent. The ARCONTA tickets demonstrate the template works at scale (82 tickets).

### Alternatives Considered
- Free-form tickets: rejected; non-deterministic, unsuitable for AI execution.
- A different template: rejected; the ARCONTA template is proven and already in use.
- Context outputs/inputs as optional: rejected; the user chose mandatory, so the Readiness Gate can resolve dependencies deterministically.

### Consequences
Positive: deterministic, reviewable, agent-ready tickets; cross-ticket data flow is explicit; consistent across projects.  
Negative: tickets must be authored to the template (the Planner/Planner-assist must enforce it, including the context declarations).

### References
00_PROJECT_BOOTSTRAP.md (Artifact Hierarchy, Aim). ID-021, ID-049, ID-064, ID-065, ID-071. Master Architecture Specification §12.9.

### Affected Components
Orchestrator (Planner), Engineering Backlog, all agent prompts.

---

## ID-071

### Title
Ticket context contract: declared outputs/inputs for deterministic cross-ticket data flow.

### Status
Accepted

### Context
Dependent tickets need facts produced by their dependencies — especially facts from Manual tickets (cloud account IDs, repo URLs, key/secret locations, endpoints) that no agent can derive. The MAS had no explicit cross-ticket context-handoff mechanism. This is the dependency-context pattern from the lean handoff (completion-notes + human-interaction-summary.context_added), made deterministic.

### Decision
Add a typed context layer over the ticket dependency DAG:
- Each ticket declares its outputs (name + type), e.g. PRD-2 outputs `aws_account_id: string`, `iam_secret_arn: secret_ref`, `gcp_project_id: string`, `maps_api_key_secret_arn: secret_ref`.
- Each ticket declares its required inputs (dependency-produced facts it needs).
- Output/input types belong to a **shared, versioned type catalogue** in `sfp-contracts` (e.g. a `context-types` set), so `aws_account_id` means the same thing across every ticket and there is no drift between `aws_account_id` and `aws_accountId`. Ad-hoc per-ticket type names are not allowed.
- The Readiness Gate (ID-064) resolves inputs: if all required inputs are available from completed dependencies, they are injected into the PR specification / agent context; if any are missing, the gate returns NEEDS_CLARIFICATION and the missing fact is requested via the CONFIRM flow (ID-069), then stored as a `context_added` fact.
- Completed-ticket outputs are persisted as structured SFP-authored facts (per ID-072: SFP-owned, Jira holds the human-facing summary).
- A **Manual ticket must declare its outputs before it can be marked complete**, so a dependent can never be blocked on an undeclared output; completion requires the declared outputs to be present.
- Secrets handling: secret outputs are **references only** (e.g. a Secrets Manager ARN / secret ID — typed `secret_ref`), never the secret value itself. The value is resolved at runtime through the secret abstraction (ID-016); it is never persisted in the context contract or in Jira.
- Hybrid: typed outputs where possible; optional natural-language completion notes for nuance that resists typing.

### Rationale
Explicit, typed input/output contracts make cross-ticket data flow machine-checkable: the Readiness Gate can fail a ticket as "not ready: missing `db_secret_arn` from PRD-11" instead of the coder discovering it mid-run (which in non-interactive v0 means INSUFFICIENT_SPECIFICATION, ID-032). A shared type catalogue prevents naming drift; requiring manual tickets to declare outputs before completion guarantees dependents always have resolvable inputs; secret references (not values) keep credentials out of the contract and Jira. This is the single highest-value adoption for a real multi-ticket project like ARCONTA.

### Alternatives Considered
- Free-text completion notes only: rejected; non-deterministic, unresolvable by the gate.
- Re-ask the human for every dependent ticket: rejected; noisy and non-deterministic.
- Ad-hoc per-ticket type names: rejected; causes naming drift across tickets.
- Allow manual tickets to complete without declaring outputs: rejected; dependents could be blocked on undeclared facts.
- Store secret values in the context contract: rejected; violates ID-016 and creates a leak risk.

### Consequences
Positive: deterministic, checkable cross-ticket data flow with a shared type catalogue; manual-ticket facts always flow to dependents; secrets stay references only.  
Negative: the type catalogue must be authored/versioned; per-ticket output/input declarations must be authored; the resolution/injection logic must be built; manual-ticket completion is gated on declared outputs.

### References
ID-064, ID-065, ID-069, ID-072. Master Architecture Specification §6, §7. Adopted/extended from `software-factory-handoff/` (completion-notes, human-interaction-summary.context_added).

### Affected Components
Orchestrator (Readiness Gate, Planner), sfp-contracts (context contract schemas), persistence (SFP-authored facts).

---

## ID-072

### Title
Architecture decision: the full event-driven MAS is the v0 target; the lean handoff is a reference design whose ideas were adopted.

### Status
Accepted

### Context
Two SFP designs existed, both authored by the user: the lean "Semi-Automated Software Factory" handoff (single Orchestrator, Jira/GitHub/Slack as systems of record, pure agents) and the full event-driven MAS (5 services, SNS/SQS, outbox, internal persistence). A decision was required on which to build.

### Decision
The full event-driven MAS (v0.1.x) is the v0 target for SFP. The lean handoff is set aside as a reference design; its high-value ideas are adopted into the full MAS/decisions (ID-065–ID-071). SFP will be built manually (bootstrap): a human mimics the Orchestrator and agents execute SFP's own blueprint tickets, because SFP does not yet exist to build itself. ARCONTA is built later, through the finished SFP.

Associated clarifications (confirming how the full MAS is read):
- Persistence: Jira and GitHub remain the source of truth for their own domains (MAS §7.8, §6.10); SFP mirrors/projects the work-lifecycle state; SFP owns its authored structured facts (PR specifications, WorkflowDecisions, readiness verdicts, agent outputs, ticket-context outputs per ID-071). SFP "business state" is the SFP-authored layer, not a duplicate of Jira.
- Agent operations boundary: the Workspace Worker/coder performs repository operations directly via the Git Provider Adapter (no raw provider credentials; inside the sandbox, ID-060), including **merge execution** — the Workspace Worker executes merges on receipt of `RequestMerge` and reports `MergeUpdated` (MAS §9.6; SFP-147, SFP-153). The Orchestrator owns the merge **decision** (emitting `RequestMerge`, SFP-138) and the other workflow-control side effects (Jira status transitions, user notifications); it never determines repository state directly. This is the MAS model (§9.6); recorded to remove ambiguity.

### Rationale
The full MAS is the user's intended scalable, decoupled, vendor-independent platform. Building it manually (bootstrap) is viable because the manual phase exercises the agent/workflow layer first (Phase A), with platform machinery (messaging, outbox, Batch) added once automation begins (Phase B). The two clarifications remove the apparent lean-vs-full tension by confirming Jira-as-source-of-truth and worker-does-repo-ops are already consistent with the MAS.

### Alternatives Considered
- Build the lean SFP first, evolve to full later: rejected; the user chose the full platform as the target. (Lean ideas still adopted.)
- Build the full platform before any manual run: rejected for sequencing; the manual bootstrap validates the agent layer first.

### Consequences
Positive: single committed architecture; lean ideas captured without leaning the platform; clear bootstrap sequence.  
Negative: the full platform is more to build than lean; phasing (A then B) is required to manage the manual bootstrap.

### References
00_PROJECT_BOOTSTRAP.md. Master Architecture Specification (full). `software-factory-handoff/` (reference). ID-060, ID-064–ID-071.

### Affected Components
All (architecture-level decision).

---

## ID-073

### Title
Git Provider credentials are role-scoped: the Coder and Reviewer use separate GitHub identities (separate tokens), never a shared one.

### Status
Accepted

### Context
SFP-41 previously specified a single `github_token_secret_ref` for all outbound GitHub operations through the Git Provider Adapter. In practice the Coder (SFP-55) pushes commits/branches and opens PRs (SFP-41/42), while the Reviewer (SFP-56) submits PR reviews (SFP-43). ID-023 makes the Reviewer **judgment-only and independent** from the Coder, and ID-066 makes review comments live on GitHub (the source of truth). If both roles authenticate as the same GitHub identity, a PR displays as the same user authoring commits *and* approving them — the automated quality gate is cosmetically undermined and the audit trail becomes ambiguous. Governance (MAS §8: the user owns accountability for any behavior-affecting change) requires a clean, distinguishable provenance for "who authored" vs "who approved."

### Decision
The Git Provider Adapter consumes **two role-scoped credentials**, injected via config (never hardcoded), parallel to the per-role model routing in ID-020/ID-063:
- `GITHUB_TOKEN_CODER` (`github_token_coder_secret_ref`) — used by the Coder for branch/push/PR operations (SFP-41, SFP-42). During the Phase A manual bootstrap this MAY be the user's personal GitHub account; in production it is a service account or the platform GitHub App acting in the coder role.
- `GITHUB_TOKEN_REVIEWER` (`github_token_reviewer_secret_ref`) — used by the Reviewer for review submission (SFP-43). This identity MUST be **distinct** from the Coder's at all phases (Phase A minimum: a dedicated `sfp-reviewer-bot` account; production: a GitHub App or separate service account).

Rules:
- The Coder NEVER authenticates with the Reviewer token, and vice versa. This is enforced at the composition root (ID-052): each role's runtime receives only its own credential.
- The Planner, Test Designer, and Readiness evaluator perform **no GitHub writes** and hold no GitHub credential.
- Credential selection is role-driven and configuration-only; no code path chooses a token based on ad-hoc logic.
- Production targets a single **GitHub App** for the platform identity, with role separation expressed as the App acting in distinct capacities (or as the App plus a distinct reviewer service account). The Phase A two-account scheme is the minimal configuration that preserves role independence and migrates cleanly to the App model.
- **Phase A token type: classic PATs, not fine-grained.** Verified during SFP-22: GitHub fine-grained PATs issued by a collaborator account **cannot write** to a repository the account does not own (the token UI exposes only "Public Repositories (read-only)" and "All repositories you own"). Because `sfp-coder-bot` / `sfp-reviewer-bot` *collaborate* on `josep-lagunas/software-factory-platform` but do not own it, fine-grained tokens return `403 Resource not accessible by personal access token` on every write, while reads succeed (the repo is public). Classic PATs use scope-based access and inherit the account's collaborator permissions, so they work in this topology. Phase A scopes: Coder → classic PAT `repo`; Reviewer → classic PAT `public_repo` (sufficient while the repo is public; `repo` if it goes private). This is broader than fine-grained least-privilege; per-repo least-privilege is restored in production via the GitHub App.
- This extends SFP-41 (and the credentials surface of SFP-43, SFP-55, SFP-56) from one token to two role-scoped tokens.

### Rationale
Separate identities make authorship and approval distinguishable on every PR, preserving the Reviewer's independence (ID-023) and the integrity of the automated quality gate. It keeps provenance auditable (MAS §8 accountability), prevents the appearance of self-review, and adds no architectural complexity — credential injection is already configuration-driven (ID-016, ID-020). The two-account Phase A minimum is the smallest change that unblocks correct governance and maps directly onto the production GitHub-App target.

### Alternatives Considered
- Single shared token (original SFP-41 design): rejected; collapses author and reviewer into one identity, undermining ID-023 independence and audit clarity.
- GitHub App from day one (Phase A): rejected for sequencing; the App is the production target but is heavier to set up than a second free account during the manual bootstrap. The two-account scheme migrates cleanly to the App.
- Fine-grained PATs for the Phase A bots: rejected (attempted and verified infeasible during SFP-22). Fine-grained PATs cannot grant write access to a repo the issuing account collaborates on but does not own; classic PATs are required until the GitHub App exists.
- Coder and Reviewer as the same bot but distinguished only in commit/review metadata: rejected; GitHub's UI and audit log key on identity, not metadata, so the self-review appearance persists.

### Consequences
Positive: clean per-role provenance on every PR; Reviewer independence enforced at the credential layer, not just by prompt; straightforward migration to a GitHub App in production.
Negative: requires a second GitHub account/token to exist and be provisioned (a new manual prerequisite — SFP-171); SFP-41/SFP-43 credential wiring is slightly broader than a single token; secret management must hold two GitHub secrets rather than one.

### References
ID-016 (secrets), ID-020 (config-driven provider selection), ID-023 (judgment-only reviewer), ID-035 (Git Provider Adapter), ID-052 (composition root), ID-063 (per-role model routing), ID-066 (review comments live on GitHub). Master Architecture Specification §8 (governance/accountability), §9.6 (Git Provider Adapter, agents). SFP-41, SFP-42, SFP-43, SFP-55, SFP-56, SFP-171.

### Affected Components
Workspace Worker (Git Provider Adapter), Agent Runtime, sfp-config, Orchestrator (no change to merge centralization, ID-072).

---

# Resolved Known Gaps (provenance)

All originally-listed known gaps have been resolved as Implementation Decisions:

- Exact Python version → ID-047
- Exact `uv` usage and package-management conventions → ID-048
- Exact testing framework and coverage target → ID-049
- Exact OpenTelemetry implementation details → ID-050
- Exact HTTP client library → ID-051
- Exact dependency-injection library beyond FastAPI dependencies → ID-052
- Transactional Outbox implementation → ID-053
- Exact LocalStack usage for SFP → ID-054
- Exact Docker Compose local development model for SFP → ID-055
- Exact release/versioning strategy → ID-056
- Exact authorization model beyond service ownership and provider authentication → ID-057
- Exact schema organization strategy for service-owned PostgreSQL persistence → ID-058
- Exact prompt file/folder organization for agents → ID-059
- Exact execution sandboxing strategy → ID-060
- Exact worker scaling thresholds → ID-061
- Exact CI/CD workflow matrix → ID-062

No open known gaps remain in this draft.

---

# Open Validation Items (to close during implementation)

These are not documentation gaps. They are empirical or implementation items that are deliberately deferred to the build phase, each already recorded in its decision's Consequences. They should become Blueprint tickets.

1. **Provider integration test (B2).** Run the Claude Agent SDK against the Z.ai GLM Anthropic-compatible endpoint and exercise the full agentic loop, including tool-calling. Outcome decides whether v0 runs on GLM or on Anthropic-direct Claude. Switching is a configuration change, so neither code nor architecture is at risk. (ID-018, ID-019)
2. **Coder/Planner model-tier downgrade experiment (ID-063).** On a sample of CodingJobs, run the candidate cheaper model and measure first-pass approval rate, the resulting rework count R, and whether the Reviewer's defect-catch rate holds. Downgrade only if `cheaper_token × (1 + R_cheap)` beats `expensive_token × (1 + R_strong)` without quality loss. The Reviewer stays on a strong model regardless.
3. **SQS→Batch bridge.** Wire the bridge (e.g. a Lambda) that submits one Batch task per dequeued Workspace Worker command, preserving the Message Bus contract (MAS §4). (ID-060, ID-061)
4. **Local secrets provider.** Implement the local secret-resolution provider behind `sfp-config` (environment / local secrets file) for local development; production uses AWS Secrets Manager (ID-016). (ID-054)
5. **eu-west-1 pricing confirmation.** Confirm current eu-west-1 pricing (Fargate, RDS PostgreSQL, NAT Gateway, CloudWatch Logs, Secrets Manager) in the AWS calculator for budgeting. (ID-015)
6. **v0 token cost (E1/E2).** Execution volume is fixed at ~36 agent executions/day (R=1, ~10 min coding / ~3 min review; ID-061). The token dollar total remains an output of items 1 and 2 (provider + model tier) and cannot be fixed until those close.
7. **Readiness rubric and evaluator prompt (ID-064).** Author and maintain the readiness rubric fields and the evaluator prompt; refine via the feedback loop from downstream `INSUFFICIENT_SPECIFICATION` failures (ID-032).

Already verified (no longer open): Python 3.13 compatibility across the full v0 stack (ID-047); the AWS backlog-per-task scaling pattern viability (ID-061, now a fallback under Batch); LocalStack Community coverage of SNS/SQS/DLQ (ID-054).
