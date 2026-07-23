

# MASTER SPECIFICATION


---

# Revision History

| Version | Date       | Author        | Description            |
|---------|------------|---------------|------------------------|
| 0.1     | 2026-06-28 | Josep Lagunas | Initial draft skeleton |
| 0.1.1   | 2026-06-29 | Josep Lagunas | Architecture review patch set: provider schema ownership, transactional outbox, query model, idempotency keys, workflow traceability, and chapter renumbering |
| 0.1.2   | 2026-06-30 | Josep Lagunas | Review aggregate clarification: explicit aggregate boundary, single-iteration semantics, immutability, and existential dependency on CodingJob |
| 0.1.3   | 2026-06-30 | Josep Lagunas | Orchestrator contract-list correction: Orchestrator-owned business events (TicketUpdated, PRSpecificationsUpdated, DeploymentUpdated, WorkflowUpdated) moved from Consumed to Produced Contracts |

---

```
EDITORIAL NOTE

The content of this document must be transcribed exactly from the frozen architecture decisions agreed during the architecture design sessions.

No chapter may be summarized, abbreviated, simplified, or rewritten for brevity.

Every architectural decision, rationale, rule, invariant, ownership definition, flow, example, note and frozen conclusion must be preserved.

This document is a compilation of the frozen architecture, not a reinterpretation of it.

If any section cannot be reproduced in full fidelity, generation must stop rather than omit or compress information.
```


# Chapter 0 — About this Document

## 0.1 Purpose

The Master Architecture Specification is the authoritative technical specification for the Software Factory Platform (SFP).

It defines the architecture, principles, service boundaries, contracts, domain model, workflows, infrastructure, and implementation constraints of the platform.

Its objective is to provide a single, complete, and consistent source of truth from which every implementation artifact can be derived.

This document is intended to be consumed by:

- Software Engineers
- Staff and Principal Engineers
- Architects
- AI coding agents
- Technical reviewers
- Future maintainers of the platform

## 0.2 Scope

This specification defines:

- Architectural principles
- System goals and non-goals
- Service decomposition
- Ownership boundaries
- Messaging architecture
- External integrations
- Commands and events
- Domain model
- State machines
- Persistence model
- Security boundaries
- Infrastructure model
- Operational workflows
- Implementation constraints

Unless explicitly stated, implementation details remain intentionally unspecified.

## 0.3 Authority

This document is the single source of truth for the Software Factory Platform architecture.

Every architectural decision must be represented here before being considered part of the platform.

The following artifacts are derived from this specification:

- Request For Comments (RFCs)
- Architecture Decision Records (ADRs)
- Implementation Blueprint
- Bootstrap Jira backlog
- UML / PlantUML diagrams
- Implementation documentation

If any derived artifact contradicts this specification, this document prevails.

## 0.4 Versioning

This specification follows Semantic Versioning.

Architecture-breaking changes require a new major version.

Backward-compatible architectural additions require a new minor version.

Editorial improvements, clarifications, diagrams, examples, and implementation guidance require a new patch version.

Current version: **0.1.3**

Status: **FROZEN (v0)**

## 0.5 Document Conventions

Every normative statement belongs to one of the following categories:

- Decision
- Rule
- Invariant
- Ownership
- Flow
- Example

Notes are informative only and never override Decisions, Rules or Invariants.

## 0.6 Evolution Process

The Software Factory Platform architecture evolves through controlled proposals.

Every architectural proposal must:

- Describe the problem.
- Explain the rationale.
- Evaluate alternatives.
- Document consequences.
- Update this specification.

RFCs and ADRs are generated from this document.

The Master Architecture Specification remains the only authoritative architectural document.

## 0.7 Design Philosophy

The Software Factory Platform has been designed according to the following principles:

- Simplicity over cleverness.
- Explicit ownership over shared responsibility.
- Immutable business facts over mutable history.
- Event-driven workflows over synchronous orchestration.
- Internal abstractions over vendor lock-in.
- Deterministic behaviour over implicit conventions.
- Architecture before implementation.

Whenever a future implementation decision conflicts with one of these principles, the architecture defined in this document takes precedence.


# Chapter 1 — Vision

## 1.1 Purpose

**Software Factory Platform (SFP) is software for building software.**

SFP is an event-driven platform that orchestrates autonomous and collaborative AI agents to transform software requirements into production-ready software.

Its objective is not to replace software engineers.

Its objective is to automate the software production process while keeping users responsible for architectural decisions, business decisions, governance and final accountability.

The platform is designed to transform software development into a deterministic, observable and continuously improvable production system.

## 1.2 Mission

SFP provides an architecture in which AI agents collaborate to transform software requirements into production-ready software.

The platform orchestrates the complete software production lifecycle, including:

- Requirement decomposition
- Implementation planning
- Code generation
- Code review
- User interaction
- Merge orchestration
- Deployment observation
- Workflow management

while maintaining complete auditability and deterministic workflows.

## 1.3 Philosophy

SFP is built around one fundamental idea:

> **Software should be produced through software.**

The platform itself becomes the software factory.

Users define:

- Goals
- Requirements
- Priorities
- Architecture
- Policies

The platform performs:

- Execution
- Coordination
- Validation
- Automation

## 1.4 Design Goals

The primary goals of SFP are:

- Deterministic workflows
- Explicit ownership boundaries
- Event-driven orchestration
- Complete auditability
- Vendor independence
- AI model independence
- User supervision when required
- Incremental evolution
- Observable software production

Every architectural decision described in this specification should support one or more of these goals.

## 1.5 Non-Goals

The platform is intentionally **not** designed to:

- Replace architectural thinking
- Replace product ownership
- Replace user accountability
- Hide implementation decisions
- Couple itself to any specific AI vendor
- Couple itself to any specific communication provider
- Couple itself to any specific source control platform

SFP is a software production platform.

It is not:

- An AI research framework
- An IDE
- A project management system

It integrates with those systems while remaining independent of them.

## 1.6 Architectural Mindset

The Software Factory Platform is designed around bounded capabilities rather than technologies.

Every capability has:

- A single owner
- Explicit responsibilities
- Explicit contracts
- Explicit boundaries

No service should require knowledge of another service's internal implementation.

Communication happens exclusively through well-defined contracts.

## 1.7 Guiding Principle

The Software Factory Platform treats software engineering as an engineering discipline rather than as an interactive activity.

Architectural decisions precede implementation.

Implementation follows deterministic workflows.

Automation is preferred whenever it improves repeatability without reducing correctness.

User intervention is introduced only when it provides architectural, business or governance value.

# Chapter 2 — Core Architectural Principles

## 2.1 Principles First

Every architectural decision in the Software Factory Platform must be traceable to one or more of the principles defined in this chapter.

These principles take precedence over implementation convenience.

When implementation and architecture conflict, the architecture defined by these principles prevails.

---

## AP-001 — Single Ownership

### Principle

Every capability has exactly one owner.

### Rule

No business capability may be owned by more than one service.

### Consequences

- Every aggregate has one owner.
- Every workflow has one owner.
- Every external provider has one owner.
- Every persistence store has one owner.

Ownership duplication is prohibited.

---

## AP-002 — Event-Driven Architecture

### Principle

Services communicate exclusively through contracts.

### Rule

Business communication happens through Commands and Events.

Services never communicate by sharing persistence.

### Consequences

- Services remain independently deployable.
- Workflows remain observable.
- Retries remain deterministic.
- Ownership boundaries remain explicit.

---

## AP-003 — Explicit Boundaries

### Principle

Every service exposes contracts.

Every service hides implementation.

### Rule

No service may depend on another service's internal implementation.

Only published contracts may be consumed.

---

## AP-004 — Business State vs Transport

### Principle

Transport messages are not business state.

Business facts are durable platform knowledge.

Transport messages are delivery mechanisms.

### Rules

Commands represent execution contracts.

Events represent business facts.

Business facts are persisted independently from message transport.

Message envelopes, SNS messages, SQS messages, retries and delivery metadata are transport concerns.

A business event may be represented as a transported message, but the transported message is not itself the authoritative business state.

### Consequences

- Messages may be discarded after successful processing.
- Persistence models never mirror transport models.
- Transport contracts may evolve independently from persistence.
- Durable business facts must be recoverable from service-owned business state, WorkflowDecisions or other authoritative service-owned persistence.
- Transport retries must never create new business facts unless the owning service determines that a new fact exists.

---

## AP-005 — Immutable Business Facts

### Principle

Business facts are immutable.

Corrections produce new facts.

### Examples

- WorkflowDecision
- UserDecision
- PRSpecification after LOCKED

### Consequences

History is append-only.

Corrections never rewrite historical facts.

---

## AP-006 — Explicit User Supervision

### Principle

Automation is preferred.

User intervention is explicit.

### Rule

Users participate only when architectural, business or governance value exists.

AI agents never silently delegate responsibility.

---

## AP-007 — Vendor Independence

### Principle

External providers are implementation details.

### Rule

Every external provider must be accessed through an internal abstraction owned by SFP.

### Examples

- LLM providers
- Git providers
- Communication providers
- Identity providers

Changing provider must not require architectural changes.

---

## AP-008 — Provider Ownership

### Principle

External providers are authenticated centrally.

External providers are interpreted by the owning service.

### Rules

The External Events Service:

- authenticates the request,
- validates endpoint configuration,
- validates transport-level request requirements,
- wraps the provider payload without interpreting it,
- publishes `ExternalEventReceived`.

Owning services:

- interpret,
- validate provider schemas,
- generate native SFP events.

No infrastructure component understands provider-specific business semantics.

---

## AP-009 — Persist Knowledge, Not Conversations

### Principle

The platform persists durable business knowledge.

It does not persist transient communication unnecessarily.

### Examples

Persist:

- UserInteraction summary
- UserDecision
- WorkflowDecision

Do not persist:

- Slack transcripts
- Raw provider conversations

---

## AP-010 — Internal Abstractions

### Principle

The platform owns its abstractions.

Vendor SDKs implement them.

### Examples

- Message Bus
- Agent Runtime
- Authentication Strategies
- Provider Schemas

No vendor SDK defines the architecture.

---

## AP-011 — Deterministic Workflows

### Principle

Workflow progression must be deterministic.

### Rule

Every workflow transition must be explainable.

Every decision must be reproducible.

Every state change must have a traceable cause.

---

## AP-012 — Software for Building Software

### Principle

The Software Factory Platform is itself a software production system.

Every capability of the platform should be designed as if it were part of a manufacturing process:

- observable,
- deterministic,
- measurable,
- continuously improvable.

Automation is introduced to improve repeatability and quality, not to remove engineering judgement.

# Chapter 3 — High-Level Architecture

## 3.1 Overview

The Software Factory Platform is an event-driven distributed system composed of independent services that collaborate through immutable contracts.

Every service owns a single business capability.

Services communicate exclusively through Commands and Events.

No service accesses another service's persistence.

This architecture follows:

- AP-001 Single Ownership
- AP-002 Event-Driven Architecture
- AP-003 Explicit Boundaries

## 3.2 Architectural Overview

The platform is composed of five business services and three shared platform components.

### Business Services

- External Events Service
- Identity Service
- Communication Service
- Orchestrator
- Workspace Worker

### Shared Platform Components

- Messaging Infrastructure
- Shared Contracts
- Common Frameworks

## 3.3 Service Responsibilities

### External Events Service

Purpose:

Receive, authenticate and normalize external webhook traffic.

Responsibilities:

- Receive webhook requests.
- Resolve endpoint configuration.
- Authenticate requests.
- Produce `ExternalEventReceived`.

Does not:

- Parse provider payloads.
- Execute business logic.
- Produce native SFP events.

Follows:

- AP-002
- AP-003
- AP-008

### Identity Service

Purpose:

Own user identity.

Responsibilities:

- User lifecycle.
- External identity mappings.
- Identity resolution.
- User lookup.

Does not:

- Own communications.
- Own workflows.
- Own projects.

### Communication Service

Purpose:

Own user communications.

Responsibilities:

- User interactions.
- Communication agent.
- Communication providers.
- User communication lifecycle.
- User notifications.
- User input requests.

Does not:

- Own workflow state.
- Interpret business meaning.
- Own user identity.

### Orchestrator

Purpose:

Own software production workflows.

Responsibilities:

- Workflow state.
- Ticket lifecycle.
- PR specifications.
- Coding jobs.
- Reviews.
- Merges.
- Deployments.
- User decisions.
- Workflow decisions.
- Scheduling.
- Business orchestration.

The Orchestrator is the authoritative owner of the software production workflow.

### Workspace Worker

Purpose:

Execute software production work.

Responsibilities:

- Agent Runtime.
- Repository management.
- Worktrees.
- Code generation.
- Code review.
- Git operations.
- Pull Request management.
- Test execution.

Workspace Workers remain stateless.

Business state belongs exclusively to the Orchestrator.

## 3.4 Shared Platform Components

### Messaging Infrastructure

Owns:

- SNS
- SQS
- DLQs
- Message Bus abstraction

Purpose:

Reliable asynchronous communication.

### Shared Contracts

Purpose:

Provide strongly typed platform communication contracts.

Includes:

- Commands
- Events
- ExternalEventReceived

Shared contracts define communication between services.

Shared contracts never define ownership.

Provider-specific schemas are intentionally excluded from Shared Contracts.

Provider schemas remain local implementation contracts owned by the service responsible for interpreting that provider.

### Common Frameworks

Purpose:

Provide reusable infrastructure.

Examples:

- Messaging framework.
- Authentication framework.
- Agent Runtime abstractions.
- Shared utilities.

Frameworks reduce duplication.

They never own business logic.

## 3.5 Ownership Matrix

| Capability | Owner |
|------------|-------|
| External Webhooks | External Events Service |
| User Identity | Identity Service |
| User Communication | Communication Service |
| Software Production Workflow | Orchestrator |
| Software Production Execution | Workspace Worker |
| Messaging Infrastructure | Platform |
| Contracts | Shared Contracts |

Every capability has exactly one owner.

Ownership overlap is prohibited.

## 3.6 Communication Model

All communication between services occurs through immutable Commands and Events transported by the messaging infrastructure.

Services never communicate by:

- Direct database access.
- Shared persistence.
- Internal implementation APIs.

Synchronous read-only queries are permitted according to the Read-Only Query Model defined in Chapter 5.

Business workflows remain asynchronous.

## 3.7 High-Level Data Ownership

External Events Service

- Endpoint Configuration

Identity Service

- User
- UserExternalIdentity

Communication Service

- UserInteraction

Orchestrator

- Business State

Workspace Worker

- No Business Persistence

Shared databases are prohibited.

## 3.8 Architectural Constraints

Every new service introduced into SFP must satisfy the following conditions:

- Own a single business capability.
- Own its persistence.
- Expose only contracts.
- Never consume another service's persistence.
- Be independently deployable.
- Be independently testable.
- Be independently replaceable.

These constraints ensure that the architecture remains evolvable over time.

# Chapter 4 — Messaging Architecture

## 4.1 Purpose

The Messaging Architecture defines how every component of the Software Factory Platform communicates.

All cross-service communication is asynchronous and event-driven.

Messaging is responsible for:

- Service decoupling
- Reliability
- Scalability
- Observability
- Fault tolerance
- Deterministic workflow progression

This chapter follows:

- AP-002 Event-Driven Architecture
- AP-003 Explicit Boundaries
- AP-004 Business State vs Transport
- AP-010 Internal Abstractions
- AP-011 Deterministic Workflows

## 4.2 Communication Model

Services never communicate through:

- Shared databases
- Direct business APIs
- Internal implementation details

Services communicate exclusively through:

- Commands
- Events

transported by the messaging infrastructure.

## 4.3 Message Types

### Commands

Commands request execution.

Properties:

- Exactly one logical consumer.
- Represent intent.
- Produce business side effects.

Examples:

- ExecuteCodingJob
- RequestUserInput
- ReviewPullRequest
- SynchronizePullRequest

### Events

Events describe immutable business facts.

Properties:

- Zero or many consumers.
- Never request execution.
- Immutable.

Examples:

- CodingJobUpdated
- ReviewUpdated
- DeploymentUpdated
- ExternalEventReceived

## 4.4 Messaging Infrastructure

The platform messaging layer consists of:

SNS → SQS → Message Handler

SNS provides fan-out.

SQS provides:

- Buffering
- Retries
- Delivery
- Back-pressure

## 4.5 Message Bus

The Message Bus is an internal platform abstraction.

Responsibilities:

- Publish Commands
- Publish Events
- Subscribe handlers
- Handle serialization
- Handle retries
- Handle correlation
- Hide SNS/SQS implementation details

Business code never interacts directly with SNS or SQS.

## 4.6 Message Handler

Consumers subscribe using the platform Message Handler abstraction.

Responsibilities:

- Deserialize contracts
- Validate messages
- Execute business logic
- Handle retries
- Handle idempotency
- Emit telemetry

## 4.7 Message Envelope

Every transported message is an instance of one of two concrete envelope
subclasses of a shared abstract `MessageEnvelope` base. The base carries the
uniform fields every message — command or event — must carry:

- message_id
- idempotency_key
- correlation_id
- causation_id
- occurred_at
- payload

The two concrete envelope subclasses are:

- `CommandEnvelope` — adds a `command_type: CommandType` discriminator.
- `EventEnvelope` — adds an `event_type: EventType` discriminator and a `producer`.

No `message_type` field is required to tell command from event: the class itself
does that. `command_type`/`event_type` instead identify WHICH specific command
or event the envelope carries.

The `payload` is the typed business data of the message. Payload classes form a
hierarchy mirroring the envelopes:

- `CommandPayload` (base) — one subclass per command.
- `EventPayload` (base) — one subclass per event.

`CommandEnvelope.payload: CommandPayload` and `EventEnvelope.payload:
EventPayload`. The discriminator is carried on the envelope ONLY — it is never
repeated inside the payload, which holds purely the command/event-specific
business data. A message is therefore a `CommandEnvelope` (or `EventEnvelope`)
instance whose `command_type` (or `event_type`) and `payload` are consistent.

Payload classes are named by a grammar convention rather than a suffix:
commands are imperative (e.g. `ExecuteCodingJob`, `ReviewPullRequest`,
`RequestMerge`); events are past-tense (e.g. `UserInputReceived`,
`CodingJobUpdated`, `MergeUpdated`). No `Command`/`Event` suffix is used.

At dispatch, the envelope is the transport wrapper: its metadata fields
(`message_id`, `correlation_id`, `causation_id`, `occurred_at` → `received_at`)
flow into the handler's `MessageContext`, and the handler receives the `payload`
plus that context — never the raw envelope.

Purpose: Tracing / Debugging / Distributed observability / Workflow
reconstruction. (`message_id` identifies a transport message; `idempotency_key`
identifies the business operation or fact; consumers must use `idempotency_key`
for business duplicate detection and must not rely on `message_id` alone.)

## 4.8 Correlation

Every workflow has one correlation identifier.

The correlation identifier propagates through:

- Commands
- Events
- User interactions
- Coding jobs
- Reviews
- Deployments

## 4.9 Delivery Guarantees

The platform assumes at-least-once delivery.

Every consumer must therefore be idempotent.

Exactly-once business processing is achieved through service-owned idempotency ledgers.

## 4.10 Idempotency

Every service owns an independent message ledger.

Purpose:

- Duplicate detection
- Retry safety
- Exactly-once business processing

Every Command and Event carries an `idempotency_key`.

The `idempotency_key` is distinct from `message_id`.

`message_id` identifies a transported message instance.

`idempotency_key` identifies the business operation, business request or business fact being processed.

A retried or redelivered transport message may have the same `idempotency_key`.

A duplicate provider webhook that represents the same external fact must resolve to the same service-owned idempotency key when interpreted by the owning service.

Consumers use the service-owned message ledger to ensure that processing the same `idempotency_key` more than once does not create duplicate business effects.

Architectural rules:

- Idempotency is owned by each consuming service.
- Business duplicate detection uses `idempotency_key`.
- Transport duplicate detection may use `message_id` but must not replace business idempotency.
- Retried messages must not create duplicate WorkflowDecisions, aggregate updates or outbound commands.
- Idempotency records are service-local operational state.


## 4.11 Transactional Message Publication

Services that persist business state and emit resulting Commands or Events must guarantee atomicity between state changes and outbound message intent.

The reference architectural pattern is a service-owned Transactional Outbox.

When a service processes a message and produces new messages, it must persist:

- the resulting business state,
- the idempotency ledger update,
- the outbound message intent,

as part of the same service-owned transaction.

Outbound messages are published from the outbox after the transaction commits.

If publication fails, the outbox entry remains pending and is retried.

A message is acknowledged only after the service has durably recorded both the business state change and the outbound message intent.

The Message Bus is responsible for transport delivery.

The owning service is responsible for durable publication intent.

Architectural rules:

- Business state and outbound message intent must be persisted atomically.
- Message publication must be retryable.
- Outbox entries must be idempotent.
- Publishing the same outbox entry more than once must not create duplicate business effects.
- Services must not rely on in-memory publication state for correctness.
- Infrastructure retries must never create additional business decisions.

## 4.12 Dead Letter Queues

Every SQS queue owns an associated Dead Letter Queue.

Messages reaching the retry limit are moved to the DLQ.

Business processing never silently discards messages.

## 4.13 Retry Strategy

Transient failures are automatically retried.

Business failures are represented as business events.

Infrastructure failures are isolated through DLQs.

## 4.14 Ordering

The platform does not assume global message ordering.

Ordering is guaranteed only where explicitly required by business workflow.

## 4.15 External Events

External providers communicate only through the External Events Service.

The External Events Service:

- Authenticates requests
- Validates endpoint configuration
- Validates transport-level request requirements
- Wraps provider payloads without interpreting them
- Publishes `ExternalEventReceived`

It never interprets provider payloads.

It never validates provider business payload schemas.

Provider payload validation belongs to the service that owns interpretation of that provider.

## 4.16 Internal Commands

Commands are point-to-point.

Every command has exactly one logical consumer, enforced by messaging topology.

## 4.17 Internal Events

Events represent immutable business facts.

Events may have zero, one or many consumers.

## 4.18 Message Versioning

Contracts evolve independently from implementation.

Rules:

- Additive fields are backward compatible.
- Breaking changes require a new contract version.
- Deprecated fields are removed only in a future major version.

## 4.19 Platform Rule

Messaging is infrastructure.

Business meaning exists only inside consuming services.

The messaging layer never owns business logic.

## 4.20 Messaging Framework

Business code never interacts directly with infrastructure.

Business code publishes through the Message Bus abstraction and consumes through the Message Handler abstraction.

## 4.21 Observability

The messaging framework automatically records:

- Published messages
- Consumed messages
- Processing duration
- Retries
- Successes
- Failures
- Correlation identifiers

Business code never implements messaging telemetry directly.

## 4.22 Architectural Rules

- Commands are point-to-point.
- Events are publish-subscribe.
- Standard SNS and Standard SQS are the reference implementation.
- Ordering is enforced by workflow logic, never by messaging infrastructure.
- Messaging infrastructure is invisible to business code.
- Commands are routed by topology, not by convention.
- Every Command and Event carries an `idempotency_key`.
- Business idempotency is based on `idempotency_key`, not only on `message_id`.
- Services that persist business state and emit messages use a service-owned Transactional Outbox or equivalent atomic persist-and-publish mechanism.
- Business facts are distinct from transport messages.

# Chapter 5 — Platform Contracts

## 5.1 Purpose

The Software Factory Platform communicates exclusively through strongly typed contracts.

Every business interaction between services is represented by one of the following contract categories:

- Commands
- Events
- External Events

Contracts are immutable.

Contracts are versioned.

Platform contracts are shared across the platform.

External Schemas are service-local implementation contracts and are not platform contracts.

This chapter follows:

- AP-002 Event-Driven Architecture
- AP-003 Explicit Boundaries
- AP-004 Business State vs Transport
- AP-010 Internal Abstractions

## 5.2 Contract Categories

The platform defines three platform contract categories.

### Commands

Purpose:

Request execution.

Properties:

- One logical consumer.
- Represent intent.
- Produce business side effects.

### Events

Purpose:

Represent immutable business facts.

Properties:

- Zero or many consumers.
- Never request execution.
- Immutable.

### External Events

Purpose:

Represent authenticated external provider payloads.

Properties:

- Produced exclusively by the External Events Service.
- Never interpreted by the External Events Service.
- Consumed only by the owning service.

Example:

- ExternalEventReceived

### External Schemas

External Schemas are not platform contracts.

Purpose:

Represent provider-specific payload validation.

External Schemas never leave the owning service.

External Schemas are implementation contracts.

Provider schemas belong to the service that owns the business interpretation of that provider.

Provider schemas never belong to Shared Contracts.

Provider schemas are never shared between services.

Examples:

Communication Service owns:

- SlackEventSchema

Orchestrator owns:

- JiraEventSchema
- GitHubWebhookSchema
- GitHubActionsEventSchema

The Workspace Worker owns GitHub operational interfaces for side effects.

The Workspace Worker does not consume or interpret GitHub webhooks.

## 5.3 Command Catalogue

The platform currently defines the following cross-service commands:

- ExecuteCodingJob
- SynchronizePullRequest
- CancelCodingJob
- ReviewPullRequest
- CancelReviewJob
- RequestUserInput
- NotifyUser
- RequestMerge

Every command is owned by exactly one consumer.

`GeneratePRSpecifications` is not a cross-service command.

PRSpecification generation is an internal Orchestrator use case.

The Orchestrator owns ticket slicing and PRSpecification generation.

The Workspace Worker owns execution of already-defined PRSpecifications.

## 5.4 Event Catalogue

The platform currently defines the following events:

- ExternalEventReceived
- TicketUpdated
- PRSpecificationsUpdated
- CodingJobUpdated
- ReviewUpdated
- UserInputReceived
- UserInteractionUpdated
- UserQueryReceived
- MergeUpdated
- DeploymentUpdated
- WorkflowUpdated

Events describe completed business facts.

Events never request execution.

## 5.5 External Event Contract

The platform defines a single ingress contract:

- ExternalEventReceived

Fields:

- external_event_id
- idempotency_key
- received_at
- provider
- endpoint_id
- headers
- payload

The payload remains opaque until interpreted by the owning service.

## 5.6 Ownership

Every platform contract has explicitly defined producer ownership.

Commands have exactly one logical consumer.

Events have zero or many consumers.

External Events are produced only by the External Events Service.

## 5.7 Versioning

Contracts evolve independently from implementation.

Rules:

- Additive fields are backward compatible.
- Breaking changes require a new contract version.
- Existing versions remain supported until explicitly deprecated.

## 5.8 Serialization

Platform contracts are defined using Pydantic.

The reference serialization format is JSON.

Serialization is an implementation detail.

The contract is the Pydantic model.

## 5.9 Contract Ownership

Contracts belong to the shared contracts package:

- sfp-contracts

No service owns shared contract definitions.

Services consume shared contracts.

Shared contracts define communication.

Shared contracts do not define business ownership.

## 5.10 Contract Evolution

Every contract modification requires:

- Architecture review.
- Semantic versioning.
- Update of this specification.

No service may privately modify a shared contract.

## 5.11 Provider Schema Rule

The platform shares business contracts, not vendor contracts.

Provider schemas are local to the service that owns interpretation of that provider.

Examples:

Slack:

- Owned by Communication Service.

Jira:

- Owned by Orchestrator.

GitHub Webhooks:

- Owned by Orchestrator.

GitHub Actions:

- Owned by Orchestrator.

GitHub outbound operational interactions:

- Owned by Workspace Worker.

This creates a clean separation:

- Inbound GitHub state changes are interpreted by the Orchestrator.
- Outbound GitHub side effects are executed by the Workspace Worker.


## 5.12 Read-Only Query Model

The Software Factory Platform is event-driven for business workflows.

However, synchronous communication is permitted for read-only information retrieval where explicitly documented.

Read-only queries exist to retrieve information owned by another service.

They must never be used to coordinate workflow progression or execute business behaviour.

Queries:

- are synchronous,
- are read-only,
- have no side effects,
- do not modify business state,
- do not emit Commands,
- do not emit Events,
- are owned by the queried service.

Business workflows remain asynchronous.

Queries retrieve information.

Commands request execution.

Events communicate business facts.

### Query Ownership

Every synchronous query belongs to the service that owns the requested information.

Examples:

Identity Service:

- ResolveUser
- ResolveExternalIdentity

Orchestrator:

- RetrieveWorkflowContext
- RetrieveProject
- RetrieveTicketSummary

Communication Service:

- RetrieveActiveInteraction

The specification intentionally describes capabilities rather than transport mechanisms.

Whether queries are implemented through REST, gRPC, RPC, libraries, or another protocol is an implementation decision.

### Architectural Rules

Queries:

- never modify aggregates,
- never trigger workflow transitions,
- never replace Commands,
- never replace Events,
- never coordinate workflows,
- never expose another service's persistence.

If an interaction modifies business state, it must be represented through Commands and Events.

Queries exist exclusively for information retrieval.

## 5.13 Architectural Rules

- Platform contracts are shared.
- Provider schemas are local.
- Commands request execution.
- Events describe facts.
- External Events wrap authenticated external payloads.
- External Schemas validate provider payloads inside owning services.
- Shared contracts are provider-agnostic.
- Provider payload semantics are never centralized in infrastructure.
- Read-only Queries retrieve information and never coordinate workflow progression.


# Chapter 6 — Domain Model

## 6.1 Purpose

The Domain Model defines the business concepts managed by the Software Factory Platform.

These concepts represent the platform state independently from:

- Persistence
- Infrastructure
- Messaging
- Implementation

The Domain Model is the canonical representation of the software production lifecycle.

This chapter follows:

- AP-001 Single Ownership
- AP-003 Explicit Boundaries
- AP-004 Business State vs Transport
- AP-005 Immutable Business Facts

## 6.2 Bounded Contexts

The platform is decomposed into four business domains.

### Identity

Owns:

- User
- UserExternalIdentity

Purpose:

Resolve user identity independently from communication providers.

### Communication

Owns:

- UserInteraction

Purpose:

Manage user communication lifecycle.

Communication does not own workflow.

Communication does not own identity.

Communication owns only communication.

### Workflow

Owns:

- Project
- ProjectUser
- Ticket
- PRSpecification
- CodingJob
- Review
- Merge
- Deployment
- UserDecision
- WorkflowDecision

Purpose:

Represent software production.

### Execution

The Workspace Worker owns no persistent business entities.

Execution is intentionally stateless.

## 6.3 Aggregate Ownership

Every aggregate has exactly one owner.

| Aggregate | Owner |
|-----------|-------|
| User | Identity Service |
| UserExternalIdentity | Identity Service |
| UserInteraction | Communication Service |
| Project | Orchestrator |
| ProjectUser | Orchestrator |
| Ticket | Orchestrator |
| PRSpecification | Orchestrator |
| CodingJob | Orchestrator |
| Review | Orchestrator |
| Merge | Orchestrator |
| Deployment | Orchestrator |
| UserDecision | Orchestrator |
| WorkflowDecision | Orchestrator |

No aggregate may have multiple owners.

## 6.4 Relationships

The domain relationships are:

```text
Project
    1 ── * ProjectUser

User
    1 ── * ProjectUser

Project
    1 ── * Ticket

Ticket
    1 ── * PRSpecification

PRSpecification
    1 ── 0..1 CodingJob

CodingJob
    1 ── * Review

CodingJob
    1 ── 0..1 Merge

Merge
    1 ── * Deployment

UserInteraction
    1 ── 0..1 UserDecision

UserDecision
    1 ── * WorkflowDecision
```

No shortcut relationships exist.

Every relationship represents business ownership or business traceability.

## 6.5 Aggregate Principles

Every aggregate:

- Owns its lifecycle.
- Owns its invariants.
- Owns its state.

Aggregates never own another aggregate's state.

Relationships exist through identifiers.

## 6.6 Business Identity

Business identifiers are immutable.

Examples:

- project_id
- user_id
- ticket_id
- pr_specification_id
- coding_job_id
- review_id
- merge_id
- deployment_id
- interaction_id
- user_decision_id
- workflow_decision_id

Identifiers are globally unique within the platform.

Business identity never depends on database implementation.

## 6.7 Immutability

Some aggregates become immutable after reaching specific lifecycle stages.

Examples:

- PRSpecification after LOCKED
- WorkflowDecision always
- UserDecision always

Corrections produce new aggregates or new facts.

Historical facts are never rewritten.

## 6.8 Persistence Ownership

Persistence is implementation.

The Domain Model does not prescribe:

- PostgreSQL
- DynamoDB
- SQL
- NoSQL

It specifies only:

- Ownership
- Relationships
- Lifecycle
- Invariants

Persistence technology is defined later.

## 6.9 Domain Rules

The following rules are architectural invariants.

### UserInteraction

- One Slack thread maps to exactly one UserInteraction in v0.
- UserInteraction owns communication lifecycle.
- UserInteraction never stores conversation transcripts.
- UserInteraction always stores a summary.
- UserInteraction expires after eight hours of inactivity.
- Closed interactions are never reopened.

### UserDecision

UserDecision represents the durable business outcome of a UserInteraction.

UserDecision belongs to the Orchestrator.

UserDecision references UserInteraction by identifier.

The reference is not a database foreign key across services.

### WorkflowDecision

WorkflowDecision represents immutable workflow decisions.

Every significant workflow transition creates exactly one WorkflowDecision.

A WorkflowDecision records why a transition happened, what policy was evaluated, which facts were considered, which commands were emitted, and what resulting state was produced.

Every workflow-affecting command emitted by the Orchestrator must be traceable to exactly one WorkflowDecision.

A WorkflowDecision is the durable audit record connecting:

- incoming business facts,
- evaluated workflow policy,
- resulting aggregate changes,
- emitted workflow-affecting commands,
- resulting workflow state.

WorkflowDecision is immutable.

Corrections require new WorkflowDecisions.

### PRSpecification

PRSpecification represents immutable implementation intent after becoming LOCKED.

Every CodingJob is based on exactly one locked PRSpecification.

A Ticket may produce multiple PRSpecifications.

A PRSpecification may produce at most one CodingJob in v0.

### CodingJob

CodingJob represents one implementation execution.

CodingJobs are non-interactive in v0.

Blocking ambiguities result in failure, never runtime user interaction.

If the PRSpecification is insufficient, the CodingJob fails with a structured reason.

### Review

A Review represents a single review iteration performed for a CodingJob.

A Review is the result of one execution of the review process. It captures the outcome of evaluating the implementation produced by a CodingJob at a specific point in time.

A CodingJob may produce multiple Reviews during its lifetime as review feedback is addressed and new review iterations are requested.

Each Review is immutable once completed. Corrections or additional review cycles produce a new Review, never modify an existing one.

A Review belongs to exactly one CodingJob.

A Review cannot exist independently. It has no lifecycle outside the CodingJob that originated it and must never be created without an associated CodingJob.

The CodingJob owns the overall implementation execution lifecycle, while each Review represents an independent evaluation of that execution.

This separation allows the platform to support:

- multiple review iterations,
- asynchronous review execution,
- different review strategies (AI, user, or hybrid),
- complete auditability of the review history,

without coupling the domain model to the review capabilities of a specific source control provider.

Reviews are not directly attached to Tickets.

Relationship:

```text
PRSpecification
        │
        ▼
CodingJob
        │
        ▼
Review*
```

Cardinality:

- One PRSpecification may produce at most one CodingJob (v0).
- One CodingJob may produce zero or many Reviews.
- Every Review belongs to exactly one CodingJob.
- A Review cannot exist without its owning CodingJob.

### Merge

Merge represents merge execution for a CodingJob.

A Merge belongs to exactly one CodingJob.

Merges are not directly attached to Tickets.

### Deployment

Deployment represents deployment observation for a merged CodingJob.

A Deployment is related to a Merge.

A Deployment is not directly attached to a Ticket.

## 6.10 Project Boundary

Project represents the logical project boundary within SFP v0.

A Project is not defined as a Jira-only concept.

Jira may be one provider that supplies or updates project-related ticket information.

The Project abstraction may evolve in future versions if the platform requires a more precise concept such as Workspace, Repository, Initiative, or Product Area.

For v0, Project is the canonical domain boundary for grouping Tickets and ProjectUser relationships.

## 6.11 Terminology Rule

The canonical term is User.

The term Human is not used anywhere in the project.

All entities, commands, events, fields, documentation, and code must use User terminology.

Examples:

- UserInteraction
- UserDecision
- UserInputReceived
- RequestUserInput
- UserInteractionUpdated

## 6.12 Domain Model Status

The Domain Model defined in this chapter is frozen for Architecture v0.

Future changes to aggregate ownership, aggregate relationships, or business terminology require a new architectural revision.

# Chapter 7 — Persistence Architecture

## 7.1 Purpose

The Persistence Architecture defines the ownership, lifecycle, and persistence responsibilities of every piece of durable information managed by the Software Factory Platform.

Persistence exists to support business capabilities.

Persistence never defines business ownership.

Business ownership always precedes persistence.

This chapter follows:

- AP-001 Single Ownership
- AP-003 Explicit Boundaries
- AP-004 Business State vs Transport
- AP-005 Immutable Business Facts

## 7.2 Persistence Principles

### Principle 1 — Persistence follows ownership

Persistence belongs to the service that owns the business capability.

No service persists another service's business state.

### Principle 2 — Persistence is never shared

Every persistence technology has exactly one owning service.

Shared databases are prohibited.

### Principle 3 — Business state is persisted independently from transport

Business state is persisted.

Transport state is not business state.

Business facts are authoritative durable knowledge.

Transport messages are delivery mechanisms for Commands and Events.

Persisted examples:

- Ticket
- UserDecision
- CodingJob
- WorkflowDecision
- Service-owned outbox entries until publication succeeds

Not persisted as business state:

- Commands
- Events
- External provider payloads
- SNS/SQS messages
- Message envelopes

Outbox entries are operational state representing durable publication intent.

They are not business state, but they are required for reliable business communication.

## 7.3 Persistence Categories

The platform defines four persistence categories.

### Business State

Purpose:

Represents durable business knowledge and workflow state.

Examples:

- Ticket
- PRSpecification
- CodingJob
- Review
- Merge
- Deployment
- UserDecision
- WorkflowDecision

Business State is authoritative.

### Operational State

Purpose:

Supports runtime execution and operational behaviour.

Examples:

- Message ledgers
- Endpoint configuration
- User interactions
- Idempotency

Operational State is service-local.

### Derived State

Purpose:

Represents information computed from primary business facts to improve usability, querying, or efficiency.

Examples:

- UserInteraction summaries
- Future search indexes
- Future reporting projections
- Future read models

Derived State can always be recomputed from authoritative sources.

Derived State never becomes the source of truth.

### External State

Purpose:

Represents information owned by external systems.

Examples:

- Jira issues
- GitHub repositories
- Slack threads
- Git history

SFP stores references and derived knowledge.

External State remains authoritative in its own domain.

## 7.4 Service Persistence Ownership

### External Events Service

Owns:

- Endpoint Configuration

Purpose:

Resolve incoming webhook endpoints.

The persistence technology is intentionally unspecified at the architecture level.

### Identity Service

Owns:

- User
- UserExternalIdentity

Purpose:

Identity resolution.

### Communication Service

Owns:

- UserInteraction
- Message Ledger
- Transactional Outbox

Purpose:

Communication lifecycle and idempotent communication processing.

No workflow information is persisted by the Communication Service.

No user identity is persisted by the Communication Service.

### Orchestrator

Owns:

- Project
- ProjectUser
- Ticket
- PRSpecification
- CodingJob
- Review
- Merge
- Deployment
- UserDecision
- WorkflowDecision

The Orchestrator is the authoritative source of business state.

Operational persistence:

- Message Ledger
- Transactional Outbox

### Workspace Worker

Owns no business persistence.

Workspace Worker execution is intentionally stateless.

If a Workspace Worker implementation durably records message processing or outbound publication intent, that state is operational state only.

It must never become business state.

Any temporary execution state is ephemeral and never considered business state.

## 7.5 Business State

Business State represents durable platform knowledge.

Examples:

- Ticket workflow
- PRSpecification lifecycle
- Coding progress
- Review status
- Merge status
- Deployment status
- User decisions
- Workflow decisions

Business State must survive:

- Process restart
- Deployment
- Infrastructure failure
- Workspace Worker failure

## 7.6 Operational State

Operational State supports execution.

Examples:

- Message ledgers
- Idempotency records
- Endpoint configuration
- User interaction lifecycle
- Transactional outbox entries
- Publication intent records

Operational State exists only to support service behaviour.

Operational State never replaces Business State.

## 7.7 Derived State

Derived State represents knowledge computed from authoritative facts.

Derived State improves usability, querying, or operational efficiency.

Derived State must always be reproducible from authoritative sources.

Examples:

- UserInteraction summaries
- Future projections
- Future read models
- Future reporting indexes

Derived State must never become the only source of a business fact.

## 7.8 External State

External providers remain the source of truth for their own domains.

Examples:

- Git repository
- Jira issue
- Slack message thread
- GitHub Pull Request
- GitHub Actions execution

SFP stores references and derived business knowledge.

SFP does not replicate provider databases.

## 7.9 Persistence Boundaries

No service may:

- Query another service's database.
- Modify another service's persistence.
- Rely on another service's storage technology.

Communication happens exclusively through contracts.

Persistence ownership is an architectural boundary.

## 7.10 Durable Knowledge

The platform persists only durable knowledge.

Persist:

- UserInteraction summary
- UserDecision
- WorkflowDecision
- PRSpecification
- CodingJob
- Review
- Merge
- Deployment

Do not persist as business state:

- Slack transcript
- GitHub webhook payload
- Jira webhook payload
- External conversations
- Raw transport messages

## 7.11 Technology Independence

The Domain Model never depends on a persistence technology.

The implementation may evolve from one database technology to another without changing the domain model.

Persistence technology is an implementation decision constrained by:

- Ownership
- Durability requirements
- Access patterns
- Operational requirements
- Architectural principles

Examples of implementation technologies may include:

- PostgreSQL
- DynamoDB
- Object storage
- Future databases

The architecture specifies ownership and invariants, not database schema design.

## 7.12 Primary Business Fact Rule

Primary business facts are authoritative.

Operational State and Derived State exist only to support platform execution.

Derived State must always be reproducible from authoritative sources.

## 7.13 Persistence Status

The Persistence Architecture defined in this chapter is frozen for Architecture v0.

Future changes to persistence ownership require an architectural revision.

Future changes to persistence technology do not require an architectural revision unless they change ownership, lifecycle, consistency, or durability guarantees.

# Chapter 8 — Workflow Architecture

## 8.1 Purpose

The Workflow Architecture defines how the Software Factory Platform transforms software requirements into production-ready software.

The workflow is deterministic.

Every state transition is explicit.

Every transition is observable.

Every transition is reproducible.

This chapter follows:

- AP-001 Single Ownership
- AP-002 Event-Driven Architecture
- AP-005 Immutable Business Facts
- AP-011 Deterministic Workflows

## 8.2 Workflow Philosophy

SFP treats software production as a finite-state workflow.

Every piece of work progresses through explicitly defined stages.

The platform never performs implicit state transitions.

Every transition:

- has a cause,
- produces observable facts,
- may produce commands,
- may produce new workflow decisions.

## 8.3 Workflow Authority

The Orchestrator is the only service authorized to advance workflow state.

No other service may directly modify workflow progression.

Other services only produce business facts.

Examples:

- Workspace Worker → CodingJobUpdated
- Communication Service → UserDecision

The Orchestrator interprets those facts and decides the next workflow transition.

## 8.4 Workflow State Machine

A Ticket progresses through the following high-level workflow states:

- READY_FOR_PR_SPECIFICATION
- READY_FOR_CODING
- CODING_IN_PROGRESS
- REVIEW_IN_PROGRESS
- WAITING_FOR_USER
- READY_FOR_MERGE
- MERGING
- DEPLOYING
- COMPLETED
- FAILED

These represent business workflow states.

They are independent from implementation.

## 8.5 Workflow Decisions

Every significant workflow transition produces a WorkflowDecision.

WorkflowDecision records:

- why the transition occurred,
- which policy was applied,
- which business facts were considered,
- which aggregate changes were produced,
- which commands were emitted,
- previous state,
- resulting state.

WorkflowDecision is immutable.

Every workflow-affecting command emitted by the Orchestrator must be traceable to exactly one WorkflowDecision.

WorkflowDecision is the authoritative explanation for why workflow state changed or why workflow-affecting work was requested.

## 8.6 Workflow Inputs

The workflow progresses only through:

- Events that represent business facts
- User Decisions

Commands never modify workflow state.

Commands request execution.

Events provide evidence.

The Orchestrator evaluates evidence before advancing workflow state.

## 8.7 Workflow Outputs

Workflow transitions may produce:

- Commands
- Events
- Notifications
- User requests

Every output is deterministic.

Every workflow-affecting output must be recorded in the WorkflowDecision that caused it.

## 8.8 Failure Handling

Failures are business facts.

They never silently disappear.

Every failure produces:

- a business event,
- a WorkflowDecision,
- an observable workflow transition.

Examples:

- Coding failed
- Review failed
- Merge failed
- Deployment failed

## 8.9 User Participation

Users influence the workflow only through UserDecision.

UserInteraction is communication.

UserDecision is business state.

The workflow never depends directly on communication providers.

## 8.10 External Systems

External systems never control workflow.

External systems provide facts.

Example:

GitHub → Merge completed.

The Orchestrator decides what that means for the workflow.

## 8.11 Scheduling

The workflow owns scheduling.

Execution systems own execution.

The Orchestrator decides what work should execute.

The Workspace Worker decides how execution is performed.

Scheduling and execution are intentionally separated.

## 8.12 Workflow Invariants

The following properties always hold:

- Every workflow has one owner.
- Every transition has one cause.
- Every transition is observable.
- Every transition is reproducible.
- Workflow state is authoritative.
- Workflow history is immutable.

## 8.13 Recovery

Workflow recovery always starts from business state.

Never from infrastructure state.

The platform reconstructs execution from:

- persisted aggregates,
- immutable WorkflowDecisions,
- immutable business events.

Infrastructure failures never invalidate workflow correctness.

## 8.14 Workflow Policies

Workflow progression is governed by policies, not hardcoded transitions.

A policy evaluates the current workflow state together with newly observed business facts and determines whether a workflow transition should occur.

Policies are deterministic and side-effect free.

They never execute work directly.

They decide:

- whether a transition is valid,
- which transition should occur,
- which commands should be emitted.

Workflow progression follows the model:

```text
Current Workflow State
        │
        ▼
Incoming Business Fact(s)
        │
        ▼
Workflow Policies
        │
        ▼
WorkflowDecision
        │
        ▼
Commands + New Workflow State
```

Workflow policies are pure functions.

Given the same inputs, a policy must always produce the same result.

Examples of workflow policies include:

- May coding start?
- Has review succeeded?
- Is user approval required?
- Can the Pull Request be merged?
- Should deployment begin?
- Should the workflow fail?

Policies evaluate business facts rather than infrastructure state.

Every WorkflowDecision records:

- evaluated policy,
- evaluated inputs,
- resulting decision,
- emitted workflow-affecting commands,
- resulting workflow transition.

Architectural rule:

Workflow behaviour is defined by policies.

Workflow transitions are the consequence of policy evaluation.

No workflow transition may bypass policy evaluation.

No workflow-affecting command may be emitted without a corresponding WorkflowDecision.

## 8.15 Workflow Status

The Workflow Architecture defined in this chapter is frozen for Architecture v0.

Future changes to workflow ownership, workflow authority or policy evaluation require an architectural revision.

# Chapter 9 — Service Specifications

## 9.1 Purpose

This chapter defines the responsibilities, ownership boundaries, public capabilities, contracts, persistence ownership, internal components, failure behaviour and implementation constraints of every service composing the Software Factory Platform.

Each service specification describes:

- Purpose
- Responsibilities
- Does Not Own
- Public Capabilities
- Consumed Contracts
- Produced Contracts
- Persistence
- Internal Components
- Architecture Constraints
- Failure Behaviour
- Observability
- Sequence Summary
- Architecture Principles Followed

Service specifications are normative.

A service may not assume responsibilities outside its specification unless the Master Architecture Specification is revised.

## 9.2 External Events Service

### Purpose

The External Events Service is the platform ingress for all external event providers.

It acts as the single entry point for externally generated events entering the Software Factory Platform.

It is responsible for authenticating requests, resolving endpoint configuration, selecting the correct authentication strategy, and publishing authenticated external events to the platform.

It never interprets provider payloads.

It never produces native SFP business events.

It never executes business logic.

### Responsibilities

The External Events Service owns:

- Webhook endpoint exposure.
- Endpoint configuration.
- Endpoint authentication.
- Endpoint configuration validation.
- Transport-level request validation.
- Authentication strategy selection.
- Authentication secret resolution.
- Construction of `ExternalEventReceived`.
- Publication of authenticated external events.

### Does Not Own

The External Events Service never owns:

- Provider-specific payload parsing.
- Provider-specific business interpretation.
- Native SFP event generation.
- Workflow progression.
- User communication.
- User identity.
- GitHub webhook semantics.
- Jira webhook semantics.
- Slack event semantics.

The External Events Service authenticates and wraps external data.

It does not understand the business meaning of that data.

It validates only ingress-level concerns.

Ingress-level validation includes:

- endpoint existence,
- endpoint status,
- authentication requirements,
- transport-level request integrity,
- required metadata for authentication.

Ingress-level validation explicitly excludes:

- provider business payload schema validation,
- provider event type interpretation,
- provider business semantics,
- workflow interpretation.

These responsibilities belong exclusively to the service that owns the corresponding provider interpretation.

### Public Capabilities

The External Events Service provides the following capabilities:

- Receive external webhook requests.
- Resolve endpoint configuration.
- Authenticate incoming requests.
- Wrap authenticated provider payloads in `ExternalEventReceived`.
- Publish authenticated external events into the platform.

### Consumed Contracts

Consumes:

- HTTP webhook requests from external providers.

### Produced Contracts

Produces:

- `ExternalEventReceived`

### Persistence

Owns:

- Endpoint Configuration

Endpoint Configuration resolves:

- endpoint_id
- provider
- authentication strategy
- secret reference
- endpoint status
- endpoint metadata

The persistence technology for endpoint configuration is intentionally unspecified at the architecture level.

### Authentication Model

Incoming requests follow the endpoint model:

```text
/webhooks/{endpoint_id}
```

The `endpoint_id` resolves:

- provider
- authentication strategy
- secret reference
- endpoint configuration

Authentication strategies are selected through the Authentication Strategy Factory.

Strategies receive injected secrets.

Strategies never load secrets themselves.

If an endpoint is unknown, the service rejects the request.

If authentication fails, the service rejects the request.

If authentication succeeds, the service publishes `ExternalEventReceived`.

### Internal Components

The External Events Service contains:

- Webhook Endpoint
- Endpoint Configuration Resolver
- Authentication Strategy Factory
- Authentication Strategies
- External Event Publisher

### Architecture Constraints

The External Events Service:

- never understands provider payload semantics,
- never parses provider payloads beyond what is required for authentication,
- never produces native business events,
- never performs workflow validation,
- never owns business state,
- never owns provider-specific business schemas.

The External Events Service validates only ingress concerns.

It never validates provider business payloads beyond what is strictly required to authenticate and safely transport the request.

Provider payload validation belongs to the service responsible for interpreting that provider.

Provider payload interpretation belongs to the service that owns the business interpretation of that provider.

### Failure Behaviour

Failure behaviour:

- Unknown endpoint produces a rejected request.
- Authentication failure produces a rejected request.
- Transient publishing failure is retried.
- Persistent publishing failure reaches the configured failure handling path.

Rejected unauthenticated requests are not published into the platform.

Authenticated requests are published as `ExternalEventReceived`.

### Observability

The service records:

- received webhook count,
- authentication successes,
- authentication failures,
- unknown endpoint requests,
- publication latency,
- publication failures,
- provider distribution,
- endpoint distribution.

### Sequence Summary

```text
External Webhook
        │
        ▼
HTTP Endpoint
        │
        ▼
Resolve Endpoint Configuration
        │
        ▼
Authentication Strategy Factory
        │
        ▼
Authenticate Request
        │
        ▼
Build ExternalEventReceived
        │
        ▼
Publish ExternalEventReceived
```

### Architecture Principles Followed

- AP-001 Single Ownership
- AP-002 Event-Driven Architecture
- AP-003 Explicit Boundaries
- AP-007 Vendor Independence
- AP-008 Provider Ownership
- AP-010 Internal Abstractions

## 9.3 Identity Service

### Purpose

The Identity Service is the authoritative owner of user identity within the Software Factory Platform.

Its responsibility is to provide a stable, platform-wide identity abstraction independently from external providers.

It enables every other service to refer to users through a single immutable identifier.

The Identity Service is responsible for resolving external identities into platform identities and platform identities into external identities.

### Responsibilities

The Identity Service owns:

- User lifecycle.
- External identity mappings.
- Identity resolution.
- User lookup.
- Provider identity lookup.

It provides a stable identity abstraction across the entire platform.

### Does Not Own

The Identity Service never owns:

- User communications.
- Workflow state.
- Project membership.
- Business permissions.
- Authorization policy.
- User interactions.
- User decisions.
- Communication providers.

Identity is intentionally separated from business responsibilities.

Project membership belongs to the Orchestrator.

Communication belongs to the Communication Service.

### Public Capabilities

The Identity Service provides the following capabilities:

- Resolve a user from an external provider identity.
- Resolve an external provider identity from a platform user.
- Retrieve platform user information.
- Resolve users by user identifier.

These capabilities are examples of read-only queries.

Their transport mechanism is intentionally unspecified and follows the Read-Only Query Model.

Whether they are exposed through REST, gRPC, RPC, library calls, or another mechanism is an implementation decision.

### Consumed Contracts

Consumes:

- None in v0.

The Identity Service may consume `ExternalEventReceived` in future versions if an identity provider such as SCIM, Okta, Azure AD or another directory source is integrated.

### Produced Contracts

Produces:

- None in v0.

No business workflow depends on asynchronous identity events in v0.

### Persistence

Owns:

- User
- UserExternalIdentity

The Identity Service is the only service allowed to modify these aggregates.

### Domain Model

#### User

Represents the canonical platform identity.

The user identifier is immutable.

Business services reference users only through `user_id`.

#### UserExternalIdentity

Maps platform users to external provider identities.

Examples:

- Slack
- GitHub
- Future providers

One user may have multiple external identities.

Each external identity belongs to exactly one user.

#### Field Sets

The persistence projection of the Identity aggregates uses these field sets.
Table names are plural snake_case (ID-058); both tables live in the `business`
schema.

`User` → `business.users`:
- `user_id` — UUID, primary key, immutable.
- `created_at` / `updated_at` — audit timestamps (ID-058 `_at` suffix).

`UserExternalIdentity` → `business.user_external_identities`:
- `external_identity_id` — UUID, surrogate primary key.
- `provider` — external provider (Slack, GitHub, future). Open-ended string;
  the valid provider set is owned elsewhere, not by this model.
- `provider_user_id` — the provider's identifier for the user.
- `user_id` — UUID, foreign key to `business.users(user_id)` (intra-service;
  ID-058 permits intra-service FKs, forbids cross-service FKs).
- `created_at` / `updated_at` — audit timestamps.
- `UNIQUE(provider, provider_user_id)` — enforces the "duplicated external
  identity" failure mode as a constraint (MAS §9.3 Failure Behaviour).

### Internal Components

The Identity Service consists of:

- Identity Resolver
- User Repository
- External Identity Repository

No provider-specific business logic exists outside these components.

### Architecture Constraints

The Identity Service:

- never communicates with users,
- never interprets business workflows,
- never owns projects,
- never owns permissions,
- never owns communication providers,
- never owns user interactions,
- never owns user decisions.

Identity remains independent from workflow and communication.

### Failure Behaviour

Identity resolution failures are explicit.

Examples:

- Unknown external identity.
- Duplicated external identity.
- Unknown user_id.
- Missing provider mapping.

Identity failures never modify workflow state directly.

They are reported back to the caller.

The caller decides how to proceed according to its own business responsibility.

### Observability

The service records:

- lookup latency,
- lookup failures,
- provider resolution metrics,
- unknown identity attempts,
- duplicate identity detection,
- external identity synchronization metrics in future versions.

No workflow telemetry exists inside the Identity Service.

### Sequence Summary

Outbound communication resolution:

```text
RequestUserInput(recipient_user_id)
        │
        ▼
Communication Service
        │
        ▼
Identity Service
        │
        ▼
Resolve external provider identity
        │
        ▼
Communication Service
```

Inbound communication resolution:

```text
Slack user id
        │
        ▼
Communication Service
        │
        ▼
Identity Service
        │
        ▼
Resolve user_id
        │
        ▼
Communication Service
```

### Architecture Principles Followed

- AP-001 Single Ownership
- AP-003 Explicit Boundaries
- AP-007 Vendor Independence
- AP-010 Internal Abstractions

## 9.4 Communication Service

### Purpose

The Communication Service is the authoritative owner of user communications within the Software Factory Platform.

Its responsibility is to establish, maintain and conclude user interactions independently from workflow execution and user identity.

It provides a provider-agnostic communication layer that enables the platform to communicate with users through one or more communication providers.

The Communication Service owns the complete lifecycle of every `UserInteraction`.

### Responsibilities

The Communication Service owns:

- User interaction lifecycle.
- Communication Agent.
- Communication provider abstraction.
- Provider-specific event interpretation for communication providers.
- User notifications.
- User input requests.
- Conversation summarization.
- Communication context management.
- Interaction expiration.
- Interaction completion.

The service is responsible for communicating with users.

It is not responsible for deciding the business meaning of those communications.

### Does Not Own

The Communication Service never owns:

- User identity.
- Business workflows.
- Ticket lifecycle.
- Project membership.
- User decisions.
- Workflow decisions.
- Software production.
- Workflow progression.

It communicates.

It does not orchestrate.

### Public Capabilities

The Communication Service provides the following capabilities:

- Send a notification to a user.
- Request information from a user.
- Receive inbound communications.
- Maintain interaction context.
- Resolve communication provider context.
- Summarize interactions.
- Produce platform communication events.

### Consumed Contracts

Consumes:

- RequestUserInput
- NotifyUser
- ExternalEventReceived where provider is Slack

### Produced Contracts

Produces:

- UserInteractionUpdated
- UserInputReceived
- UserQueryReceived

### Persistence

Owns:

- UserInteraction
- Message Ledger

No workflow state is persisted.

No user identity is persisted.

### Domain Model

#### UserInteraction

Represents one bounded business communication.

A `UserInteraction` maps 1:1 to a Slack thread in v0.

The interaction is the authoritative representation of that communication.

The Slack thread is the provider implementation.

#### UserInteraction Fields

A `UserInteraction` contains:

- interaction_id
- user_id, when resolvable
- origin
- type
- response_required
- channel
- provider_reference
- question
- summary
- last_message_emissor
- last_message_timestamp
- created_at
- expires_at
- completed_at
- previous_interaction_id or context_interaction_id, when applicable

The canonical term is `last_message_emissor` as frozen during the architecture design.

`origin` indicates whether the interaction was initiated inbound or outbound.

`last_message_emissor` indicates who sent the most recent message.

These are different concepts.

### UserInteraction Lifecycle

A `UserInteraction` begins when a communication objective appears.

A `UserInteraction` ends when:

- it is completed, or
- it expires.

Completed interactions are immutable.

Expired interactions are immutable.

Neither may be reopened.

### Summary

Every `UserInteraction` contains a mandatory summary.

The summary is the durable representation of the interaction.

Conversation transcripts are intentionally not persisted.

### Expiration

A `UserInteraction` expires after eight hours of inactivity.

Inactivity is measured from `last_message_timestamp`.

Every inbound or outbound message updates:

- last_message_emissor
- last_message_timestamp

The expiration timer is reset after every message.

### Closed Interactions

If a user replies to a completed or expired interaction:

- the interaction remains closed,
- the Communication Service does not reopen the interaction,
- the Communication Service requests the user to start a new thread,
- the previous interaction identifier is provided as context reference,
- the new interaction may use the previous interaction summary as contextual history.

### Communication Agent

The Communication Agent owns:

- communication understanding,
- interaction summarization,
- response generation,
- communication context reconstruction.

The Communication Agent is not session-based.

Context is reconstructed from:

- UserInteraction summary,
- current incoming message,
- interaction metadata,
- provider context,
- business information retrieved from the Orchestrator through read-only queries when required.

### Communication Policies

The Communication Service defines the policies governing every user interaction.

The Communication Agent executes those policies.

The Communication Agent never defines platform behaviour.

Examples of Communication Service policies include:

- A Slack thread maps 1:1 to a UserInteraction.
- A UserInteraction expires after eight hours of inactivity.
- Completed or expired interactions are immutable.
- Closed interactions are never reopened.
- A new message received in a closed interaction results in a request to start a new interaction.
- Every UserInteraction must maintain a durable summary.
- Conversation transcripts are never persisted.

These policies are architectural invariants.

The Communication Agent must operate within these constraints.

It is not allowed to override, reinterpret or bypass them.

Architectural rule:

```text
The platform defines communication policies.
The Communication Agent executes communication policies.
Artificial Intelligence executes policy.
The platform defines policy.
```

### Communication Providers

Communication providers are implementation details.

The service currently supports Slack in v0.

Future providers may be added without changing the platform architecture.

Examples:

- Microsoft Teams
- Email
- WhatsApp

### Architecture Constraints

The Communication Service:

- never owns user identity,
- never owns workflow progression,
- never interprets business meaning,
- never owns project information,
- never owns provider authentication.

Provider authentication belongs to the External Events Service.

Identity belongs to the Identity Service.

Business interpretation belongs to the Orchestrator.

### Failure Behaviour

Communication failures are communication failures.

They never directly modify workflow state.

Examples:

- Slack unavailable.
- User unreachable.
- Provider timeout.
- Unknown provider identity.

Workflow progression remains the responsibility of the Orchestrator.

### Observability

The Communication Service records:

- interaction creation,
- interaction completion,
- interaction expiration,
- provider latency,
- communication failures,
- communication agent metrics,
- interaction summarization metrics.

### Sequence Summary

Outbound communication:

```text
RequestUserInput
        │
        ▼
Resolve User Identity
        │
        ▼
Create UserInteraction
        │
        ▼
Communication Agent
        │
        ▼
Communication Provider
```

Inbound communication:

```text
ExternalEventReceived
        │
        ▼
Provider Schema
        │
        ▼
Communication Agent
        │
        ▼
Update UserInteraction
        │
        ▼
Publish UserInputReceived
or
Publish UserQueryReceived
```

### Architecture Principles Followed

- AP-001 Single Ownership
- AP-002 Event-Driven Architecture
- AP-003 Explicit Boundaries
- AP-007 Vendor Independence
- AP-008 Provider Ownership
- AP-009 Persist Knowledge, Not Conversations
- AP-010 Internal Abstractions

## 9.5 Orchestrator

### Purpose

The Orchestrator is the authoritative owner of the software production workflow.

It owns workflow state, workflow decisions and software production coordination.

It evaluates business facts, applies workflow policies, produces WorkflowDecisions and coordinates every stage of software production.

The Orchestrator is the only service allowed to modify software production business state.

It never executes software production work itself.

It coordinates services that do.

### Responsibilities

The Orchestrator owns:

#### Workflow

- Workflow lifecycle.
- Workflow state transitions.
- Workflow scheduling.
- Workflow policies.
- Workflow decisions.

#### Software Production

- Ticket lifecycle.
- PRSpecification lifecycle.
- CodingJob lifecycle.
- Review lifecycle.
- Merge lifecycle.
- Deployment lifecycle.

#### User Decisions

- UserDecision.
- Business interpretation of user input.

#### Scheduling

The Orchestrator decides:

- what happens next,
- when it happens,
- why it happens.

It never decides how execution is performed.

### The Orchestrator Is Not

The Orchestrator is intentionally not:

- a communication service,
- an identity service,
- a Git service,
- a coding service,
- a review service,
- a deployment service.

It owns decisions.

It does not own execution.

### Public Capabilities

The Orchestrator provides the following capabilities:

- Accept business facts.
- Evaluate workflow policies.
- Produce workflow decisions.
- Advance workflow state.
- Schedule platform work.
- Coordinate software production.
- Interpret user decisions.
- Interpret workflow-relevant provider facts.
- Provide read-only workflow context queries.
- Provide read-only ticket and project information queries.

### Consumed Contracts

Consumes:

- CodingJobUpdated
- ReviewUpdated
- MergeUpdated
- UserInputReceived
- UserInteractionUpdated, when relevant
- ExternalEventReceived for Jira
- ExternalEventReceived for GitHub
- ExternalEventReceived for GitHub Actions

### Produced Contracts

Produces:

Commands:

- ExecuteCodingJob
- ReviewPullRequest
- SynchronizePullRequest
- CancelCodingJob
- CancelReviewJob
- RequestUserInput
- NotifyUser
- RequestMerge

Business events:

- TicketUpdated
- PRSpecificationsUpdated
- DeploymentUpdated
- WorkflowUpdated

The Orchestrator also produces business events resulting from workflow progression.

`GeneratePRSpecifications` is not a cross-service command.

PRSpecification generation is an internal Orchestrator use case.

### Persistence

The Orchestrator owns the authoritative business state.

Owned aggregates:

- Project
- ProjectUser
- Ticket
- PRSpecification
- CodingJob
- Review
- Merge
- Deployment
- UserDecision
- WorkflowDecision

No other service may modify these aggregates.

### Internal Components

#### Workflow Engine

Responsible for maintaining workflow progression.

It owns workflow state.

#### Policy Engine

Responsible for evaluating workflow policies.

Input:

```text
Current State
+
Business Facts
```

Output:

```text
WorkflowDecision
```

Policies are deterministic.

Policies never execute work.

#### Scheduler

The Scheduler is an internal component of the Orchestrator responsible for execution planning.

The Scheduler owns:

- execution dispatch,
- execution prioritization,
- execution concurrency,
- execution admission control.

The Scheduler determines when work is executed.

It never determines what work should be executed.

That responsibility belongs to the Policy Engine.

The Scheduler consumes workflow decisions and transforms them into executable commands while respecting platform execution policies and resource constraints.

Examples include:

- Maximum concurrent CodingJobs.
- Maximum concurrent ReviewJobs.
- Future prioritization strategies.
- Future execution quotas.
- Future tenant fairness.

The Scheduler is deterministic.

It never changes workflow state.

It only determines when execution-bound commands are emitted.

Architectural rule:

```text
Workflow decides WHAT should happen.
Scheduler decides WHEN it should happen.
Execution decides HOW it happens.
```

#### Aggregate Manager

Responsible for maintaining aggregate consistency.

Every aggregate modification occurs through aggregate-level business rules.

#### Decision Recorder

Responsible for persisting WorkflowDecision.

Every significant business decision is recorded.

No workflow transition occurs without a corresponding WorkflowDecision.

No workflow-affecting command is emitted without being recorded in the WorkflowDecision that caused it.

### Workflow Authority

The Orchestrator is the only service allowed to:

- advance workflow state,
- schedule work,
- interpret business facts,
- produce WorkflowDecision.

No other service owns these responsibilities.

### Architecture Constraints

The Orchestrator:

- never communicates directly with users,
- never resolves user identities,
- never owns communication providers,
- never executes Git operations,
- never executes coding work,
- never executes review work,
- never authenticates external webhook providers.

Every external capability is delegated.

### Failure Behaviour

Failures are business facts.

Failures never silently disappear.

Every failure:

- produces a WorkflowDecision,
- produces observable workflow progression,
- may produce new commands,
- may terminate the workflow.

### Observability

The Orchestrator records:

- workflow progression,
- workflow decisions,
- scheduling decisions,
- policy evaluations,
- aggregate modifications,
- command emissions,
- event consumption.

The Orchestrator is the primary source for workflow observability.

### Sequence Summary

```text
Business Fact
        │
        ▼
Aggregate Update
        │
        ▼
Policy Evaluation
        │
        ▼
WorkflowDecision
        │
        ▼
Scheduler
        │
        ▼
Command Emission
        │
        ▼
Message Bus
```

Relationship between internal components:

```text
Business Facts
        │
        ▼
Workflow Engine
        │
        ▼
Policy Engine
        │
        ▼
WorkflowDecision
        │
        ▼
Scheduler
        │
        ▼
Commands
        │
        ▼
Workspace Worker
```

### Architecture Principles Followed

- AP-001 Single Ownership
- AP-002 Event-Driven Architecture
- AP-003 Explicit Boundaries
- AP-005 Immutable Business Facts
- AP-011 Deterministic Workflows

## 9.6 Workspace Worker

### Purpose

The Workspace Worker is the execution engine of the Software Factory Platform.

Its responsibility is to execute software production tasks requested by the Orchestrator.

It performs software engineering work.

It never owns software engineering decisions.

The Workspace Worker transforms commands into execution.

It never transforms business facts into workflow progression.

### Responsibilities

The Workspace Worker owns:

#### Agent Runtime

- Coding Agent
- Review Agent
- Future execution agents

#### Repository Execution

- Repository checkout
- Worktree lifecycle
- Branch lifecycle
- Local execution environment

#### Git Operations

- Commit
- Push
- Branch synchronization
- Pull Request creation
- Pull Request review submission
- Merge execution


#### Merge Execution

Merge execution is an execution responsibility.

The Orchestrator determines whether a CodingJob is eligible for merge by evaluating workflow policies.

When a merge is approved, the Orchestrator emits the `RequestMerge` command.

The Workspace Worker executes the merge operation through the Git Provider Adapter and reports the execution result by publishing `MergeUpdated`.

The Workspace Worker never determines whether a merge should occur.

It executes only explicitly requested merge operations.

#### Local Validation

- Build
- Tests
- Linters
- Static analysis

#### Execution Reporting

- Produce execution events describing the outcome of work.

### Does Not Own

The Workspace Worker never owns:

- Workflow state
- Business state
- User identity
- User communication
- Workflow decisions
- Scheduling
- Policy evaluation
- Ticket lifecycle
- Provider webhook interpretation

The Workspace Worker executes.

It never orchestrates.

### Public Capabilities

The Workspace Worker provides the following capabilities:

- Execute coding work.
- Execute review work.
- Synchronize Pull Requests.
- Execute merge operations.
- Execute local validation.
- Interact with source control.
- Produce execution results.

### Consumed Contracts

Consumes:

- ExecuteCodingJob
- ReviewPullRequest
- SynchronizePullRequest
- RequestMerge
- CancelCodingJob
- CancelReviewJob

Only execution commands are consumed.

No workflow events are interpreted.

### Produced Contracts

Produces:

- CodingJobUpdated
- ReviewUpdated
- MergeUpdated

Execution produces facts.

Execution never produces workflow decisions.

### Persistence

The Workspace Worker owns no business persistence.

Persistent business state belongs exclusively to the Orchestrator.

The Workspace Worker may use ephemeral execution state such as:

- temporary worktrees,
- local caches,
- temporary agent context,
- temporary build artifacts.

These are implementation details and are never considered business state.

### Execution Model

The Workspace Worker is a logical execution service composed of one or more identical worker instances.

The platform is designed for horizontal scalability.

All Workspace Worker instances are considered equivalent.

They differ only by the work they are currently executing.

Workers:

- share no business state,
- own no workflow state,
- own no persistent business data,
- execute commands independently.

Workers compete for executable commands through the messaging infrastructure.

Concurrency is controlled by:

- the Scheduler, which decides when work becomes executable,
- the messaging infrastructure, which distributes work,
- the number of running Workspace Worker instances.

Execution capacity is increased by adding more worker instances.

No architectural changes are required to scale execution horizontally.

Architectural rule:

```text
Workspace Workers are stateless execution nodes.
The platform scales execution horizontally by increasing the number of Workspace Worker instances.
Business correctness is independent of the number of running workers.
```

Consequences:

- No sticky sessions.
- No leader election.
- No worker affinity.
- No shared caches required for correctness.
- Any worker can execute any compatible command.

### Internal Components

#### Execution Coordinator

Coordinates the execution of incoming commands.

It allocates execution resources and dispatches work to the appropriate execution agent.

#### Agent Runtime

Hosts and executes autonomous software engineering agents.

Responsibilities:

- prompt construction,
- tool execution,
- execution loop,
- model interaction,
- execution summary generation.

The Agent Runtime is an internal abstraction.

Its implementation may evolve independently from the platform architecture.

#### Repository Manager

Responsible for:

- repository cloning,
- worktree management,
- branch management,
- repository cleanup.

#### Git Provider Adapter

Responsible for all outbound GitHub interactions.

Examples:

- create Pull Request,
- update Pull Request,
- submit review,
- synchronize branch.

Inbound GitHub webhooks are explicitly not handled here.

#### Local Execution Engine

Responsible for:

- tests,
- build,
- linting,
- static analysis,
- execution of project-specific tooling.

### Agent Runtime

The Workspace Worker owns the platform Agent Runtime.

The Agent Runtime is an internal abstraction.

The platform architecture never depends on a specific LLM vendor or agent framework.

The runtime may be implemented using:

- Claude Code
- GLM
- Claude
- GPT
- Future models
- Future agent frameworks

without changing the platform architecture.

SFP owns the Agent Runtime abstraction.

Vendor SDKs may implement it.

Vendor SDKs must not define the platform architecture.

### Coding Agent Policy

The Coding Agent operates in non-interactive mode for v0.

The Coding Agent:

- implements the supplied PRSpecification,
- makes reasonable assumptions,
- documents assumptions,
- never requests user clarification during execution.

Blocking ambiguities result in:

```text
CodingJobUpdated
status = FAILED
reason = INSUFFICIENT_SPECIFICATION
```

User clarification is handled by the Orchestrator through a new workflow iteration.

No paused coding jobs exist in v0.

No `ResumeCodingJob` command exists in v0.

### Architecture Constraints

The Workspace Worker:

- never owns workflow,
- never owns business state,
- never communicates directly with users,
- never interprets provider webhooks,
- never authenticates external providers,
- never owns scheduling,
- never owns policy evaluation.

The Workspace Worker is intentionally execution-only.

### Failure Behaviour

Execution failures produce execution facts.

Examples:

- coding failed,
- review failed,
- build failed,
- tests failed,
- git operation failed.

Execution failures never modify workflow state directly.

The Orchestrator interprets those facts.

### Observability

The Workspace Worker records:

- execution duration,
- agent execution metrics,
- repository metrics,
- tool execution metrics,
- model interaction metrics,
- execution failures.

Business observability belongs to the Orchestrator.

### Sequence Summary

```text
ExecuteCodingJob
        │
        ▼
Execution Coordinator
        │
        ▼
Agent Runtime
        │
        ▼
Repository Manager
        │
        ▼
Git Provider Adapter
        │
        ▼
CodingJobUpdated
```

### Architecture Principles Followed

- AP-001 Single Ownership
- AP-002 Event-Driven Architecture
- AP-003 Explicit Boundaries
- AP-007 Vendor Independence
- AP-010 Internal Abstractions
- AP-011 Deterministic Workflows

## 9.7 Chapter Status

The Service Specifications defined in this chapter are frozen for Architecture v0.

Every service owns exactly one bounded capability.

No service may expand its responsibility without an architectural revision.

# Chapter 10 — Infrastructure Architecture

## 10.1 Purpose

The Infrastructure Architecture defines the execution environment of the Software Factory Platform.

Infrastructure exists to host, secure and operate the platform.

Infrastructure never defines business behaviour.

Business architecture remains independent from infrastructure choices.

This chapter follows:

- AP-001 Single Ownership
- AP-002 Event-Driven Architecture
- AP-003 Explicit Boundaries
- AP-007 Vendor Independence
- AP-010 Internal Abstractions

## 10.2 Infrastructure Principles

### Principle 1

Infrastructure supports architecture.

Architecture never adapts to infrastructure limitations.

### Principle 2

Every deployable service owns its own infrastructure.

Infrastructure ownership follows business ownership.

### Principle 3

Infrastructure is reproducible.

Every infrastructure component is provisioned through Infrastructure as Code.

Manual infrastructure changes are prohibited.

## 10.3 Deployment Model

The platform is composed of independently deployable services:

- External Events Service
- Identity Service
- Communication Service
- Orchestrator
- Workspace Worker

Every service may be deployed independently.

Every service may scale independently.

## 10.4 Infrastructure Ownership

Each service owns:

- Compute
- Queues
- Dead Letter Queues
- Message Ledger
- IAM permissions
- Configuration
- Secrets

Shared platform infrastructure is limited to:

- Networking
- Relational persistence platform
- Messaging backbone
- Monitoring infrastructure

## 10.5 Platform Infrastructure

The reference implementation targets AWS.

Shared infrastructure includes:

- VPC
- Container orchestration platform (AWS ECS for v0)
- SNS
- PostgreSQL cluster
- CloudWatch
- AWS Secrets Manager

The architecture remains cloud-independent.

AWS is the reference implementation, not an architectural requirement.

## 10.6 Infrastructure as Code

Infrastructure is defined using Pulumi.

The platform is divided into:

- Platform Infrastructure
- Per-Service Infrastructure

Platform Infrastructure provisions:

- Networking
- Shared messaging
- Shared observability
- Shared relational persistence platform

Each service provisions:

- Compute
- Queues
- IAM
- Configuration
- Secrets

## 10.7 Configuration

Configuration follows service ownership.

Each service owns:

- Configuration
- Environment variables
- Secret references

Configuration is injected.

Configuration is never hardcoded.

## 10.8 Secrets

Secrets are centrally managed.

Reference implementation:

- Pulumi encrypted configuration
- AWS Secrets Manager

Secrets are injected into services.

Application code never reads encrypted configuration directly.

## 10.9 Repository Structure

The platform is maintained as a monorepository.

Each service is an independent Python project containing:

- src/
- tests/
- infrastructure/
- pyproject.toml
- README.md

Shared packages remain independent projects.

## 10.10 Internal Structure

Every service follows the same internal organization:

- domain/
- application/
- interfaces/
- infrastructure/
- entrypoints/

Uniformity across services is mandatory.

## 10.11 Observability

Observability is platform-wide.

Infrastructure provides:

- Logging
- Metrics
- Distributed tracing
- Health monitoring

Frameworks provide baseline observability automatically.

Business services may emit domain-level observability through platform observability abstractions.

Business services must not implement vendor-specific telemetry directly.

## 10.12 Deployment

Deployment is performed through GitHub Actions.

Deployment pipelines are version controlled.

Deployment workflows are infrastructure.

## 10.13 Scaling

Every service scales independently.

Examples:

- Additional Workspace Workers
- Additional Communication Service instances
- Additional External Events Service instances

The Orchestrator remains logically unique while allowing redundant deployment for availability.

## 10.14 Shared Infrastructure

Infrastructure resources may be physically shared.

Ownership is never shared.

Examples:

One PostgreSQL cluster hosting multiple logical databases:

- Identity database
- Orchestrator database

One SNS infrastructure hosting independent topics.

One VPC hosting independent services.

Architectural rule:

Shared infrastructure is permitted.

Shared ownership is prohibited.

## 10.15 Constraints

Infrastructure decisions must never violate architectural ownership.

Infrastructure must not introduce:

- Shared business databases
- Cross-service persistence
- Direct service coupling

Infrastructure serves architecture.

Architecture never serves infrastructure.

## 10.16 Infrastructure Status

The Infrastructure Architecture defined in this chapter is frozen for Architecture v0.

Future infrastructure technology changes do not require architectural revision provided ownership boundaries and architectural principles remain unchanged.



# Chapter 11 — Operational Model

## 11.1 Purpose

The Operational Model defines how the Software Factory Platform behaves while running.

It specifies the operational lifecycle of services, message processing, scheduling, execution, recovery and observability.

This chapter intentionally focuses on runtime behaviour rather than software architecture.

It answers the question:

> How does SFP behave once deployed?

This chapter follows:

- AP-002 Event-Driven Architecture
- AP-003 Explicit Boundaries
- AP-010 Internal Abstractions
- AP-011 Deterministic Workflows

## 11.2 Service Lifecycle

Every service follows the same operational lifecycle:

1. Load configuration.
2. Load secrets.
3. Validate configuration.
4. Initialize infrastructure clients.
5. Initialize internal components.
6. Register message handlers.
7. Expose health endpoints.
8. Begin consuming messages.
9. Enter Running state.

A service is never considered Running until it is capable of safely processing work.

## 11.3 Startup Rules

Startup failures fail fast.

Partial startup is prohibited.

A service must not consume messages until startup completes successfully.

## 11.4 Graceful Shutdown

Shutdown follows this sequence:

1. Stop accepting new work.
2. Finish in-flight work.
3. Durably preserve pending outbox entries.
4. Flush telemetry.
5. Release resources.
6. Terminate.

A service must never abandon partially processed business state.

## 11.5 Message Processing Lifecycle

Every incoming message follows:

1. Receive.
2. Deserialize.
3. Validate.
4. Idempotency check using `idempotency_key`.
5. Execute business logic.
6. Persist business state, idempotency records and outbound message intent atomically.
7. Acknowledge the processed message.
8. Publish resulting messages from the service-owned outbox.

Messages are acknowledged only after business state, idempotency records and outbound message intent have been durably recorded.

Outbound publication may occur after acknowledgement because the service-owned outbox preserves publication intent.

## 11.6 Failure Model

Failures are classified into:

### Infrastructure Failure

Examples:

- Network
- SNS
- SQS
- Database connectivity

Handling: automatic retry.

### Execution Failure

Examples:

- Build failure
- Test failure
- Git conflict

Handling: business event. Workflow continues according to policy.

### Business Failure

Examples:

- Insufficient specification
- Invalid transition
- Policy rejection

Handling: WorkflowDecision. No infrastructure retry.

## 11.7 Retry Behaviour

Retries are automatic only for transient failures.

Business failures are never retried automatically.

Every retry remains idempotent.

Retry behaviour belongs to the messaging framework.

## 11.8 Scheduler Behaviour

The Scheduler owns execution admission.

Responsibilities:

- Concurrency limits
- Execution prioritization
- Command dispatch
- Execution fairness

The Scheduler controls admission of execution-bound commands only.

Communication commands such as `NotifyUser` and `RequestUserInput` are emitted immediately.

## 11.9 Workspace Worker Behaviour

Workspace Workers:

- Execute independently.
- Remain stateless.
- Publish execution results.
- Never own workflow.

Workers may be added or removed without affecting business correctness.

## 11.10 Capacity Management

Execution capacity is controlled by:

- Scheduler policies
- Number of Workspace Workers
- Queue depth

Scaling workers increases throughput.

No workflow changes are required.

## 11.11 Health Model

Every service exposes:

### Liveness

Indicates whether the process is alive.

### Readiness

Indicates whether the service is capable of safely processing work.

Only ready services consume messages.

## 11.12 Observability

Observability is automatic.

Frameworks record:

- Logs
- Metrics
- Traces
- Message timings
- Retries
- Failures
- Command execution
- Event publication

Business code emits domain-level observability only through platform observability abstractions.

Business code must not implement vendor-specific observability directly.

## 11.13 Recovery

Recovery always starts from durable state.

After restart, services:

- Reload configuration.
- Reconstruct internal state.
- Resume message consumption.

Normal infrastructure failures require no manual recovery.

## 11.14 Operational Invariants

The following always hold:

- Services start before consuming work.
- Services stop consuming before shutdown.
- Messages are acknowledged only after successful processing.
- Business state, idempotency records and outbound message intent are persisted atomically before acknowledgement.
- Workspace Worker failures never corrupt workflow state.
- Infrastructure failures never invalidate business correctness.
- Outbox publication retries never create duplicate business effects.
- Business idempotency is based on `idempotency_key`.

## 11.15 Back-pressure Model

Back-pressure is absorbed by the messaging infrastructure.

Primary elasticity is provided by SQS queues.

Queue depth represents accumulated work.

Queue depth is not a failure.

Architectural rule:

- Back-pressure is absorbed by queues.
- Business services remain responsive.
- Workers consume work according to available execution capacity.

Scaling behaviour:

- Queue depth increases under load.
- Worker instances reduce queue depth.
- Business correctness remains unchanged.

## 11.16 Operational Status

The Operational Model defined in this chapter is frozen for Architecture v0.

Future operational improvements must preserve deterministic workflow execution and service ownership boundaries.
# Chapter 12 — Future Evolution and Explicit Non-Goals

## 12.1 Purpose

This chapter defines the architectural boundaries of Software Factory Platform v0.

It explicitly documents capabilities intentionally excluded from the initial version, together with the architectural extension points that enable future evolution.

The absence of a capability from v0 should not be interpreted as an architectural limitation.

It should be interpreted as an implementation scope decision.

This chapter also defines how the architecture should evolve after v0.1.0.

The Master Architecture Specification remains the source of truth for every future architectural revision.

## 12.2 Explicit Non-Goals

The following capabilities are intentionally excluded from v0.

Excluding them from v0 does not mean the architecture cannot support them.

It means they are not part of the initial implementation scope.

### Interactive Coding Agents

Coding agents execute in non-interactive mode in v0.

If a PRSpecification is insufficient, execution fails.

The Coding Agent must not pause execution and wait for user clarification.

The Coding Agent must not initiate a live clarification loop with the user.

User clarification results in a new software production iteration rather than an interactive coding session.

This keeps the v0 execution model deterministic.

It also avoids having paused coding jobs occupying execution capacity while waiting for user input.

The platform may introduce interactive coding agents in a future version, but that requires an architectural revision covering:

- paused execution state,
- persisted user responses,
- correlation between questions and answers,
- resume semantics,
- execution capacity management,
- timeout behaviour,
- failure behaviour.

### Multi-Provider Communication

v0 supports Slack as the communication provider.

The architecture allows additional communication providers without changing the platform boundaries.

Future providers may include:

- Microsoft Teams,
- Email,
- WhatsApp,
- other communication systems.

Additional providers must be implemented inside the Communication Service.

They must not change the ownership of UserInteraction.

They must not introduce provider-specific concepts into shared contracts.

They must not bypass the External Events Service for inbound webhook authentication.

### Multi-Provider Identity

v0 supports only the identity mappings required by the initial platform integrations.

Future identity providers may be integrated through the Identity Service.

Examples include:

- SCIM,
- Okta,
- Azure AD,
- Google Workspace,
- GitHub identity,
- Slack identity.

Identity provider integrations must not move identity ownership outside the Identity Service.

Other services must continue to reference users by platform `user_id`.

### Advanced Scheduling

The Scheduler currently performs deterministic execution admission.

Future versions may introduce advanced scheduling policies such as:

- priorities,
- quotas,
- fairness,
- tenant isolation,
- execution aging,
- resource-aware scheduling,
- per-project concurrency limits,
- per-repository concurrency limits.

No architectural changes are required as long as scheduling remains owned by the Orchestrator and execution remains owned by the Workspace Worker.

The Scheduler decides when execution-bound commands are emitted.

It must not decide what work should happen.

It must not execute work.

### Advanced Agent Coordination

v0 defines a single execution runtime abstraction.

Future versions may introduce:

- specialized planning agents,
- specialized coding agents,
- specialized review agents,
- collaborative multi-agent execution,
- agent negotiation,
- agent self-evaluation,
- agent handoff,
- agent voting,
- agent specialization by repository or language.

These capabilities must remain behind the Agent Runtime abstraction.

Agent frameworks and model SDKs must not define the platform architecture.

The platform defines policy.

Agents execute policy.

### Multi-Tenancy

The architecture is prepared for multi-tenancy, but tenant isolation is intentionally excluded from v0.

Future multi-tenancy may require:

- tenant identifiers,
- tenant isolation policies,
- tenant-aware scheduling,
- tenant-specific quotas,
- tenant-scoped secrets,
- tenant-scoped configuration,
- tenant-scoped observability,
- tenant-specific data isolation.

Introducing multi-tenancy requires an architectural revision.

Multi-tenancy must not violate service ownership boundaries.

### Distributed Execution Policies

v0 assumes homogeneous Workspace Worker instances.

Future versions may introduce heterogeneous execution environments such as:

- language-specific workers,
- GPU workers,
- repository-specific workers,
- provider-specific execution pools,
- high-memory workers,
- sandboxed workers,
- isolated security workers.

The Workspace Worker architecture supports horizontal specialization.

Any future specialization must preserve the rule that Workspace Workers own execution only and never own workflow state.

### Interactive Planning Service

v0 keeps PRSpecification generation inside the Orchestrator as an internal use case.

There is no separate Planner Service in v0.

There is no cross-service `GeneratePRSpecifications` command in v0.

Future versions may extract planning into a dedicated service only if that service receives a clear bounded capability and ownership model.

Until such a revision exists, ticket slicing and PRSpecification generation remain Orchestrator responsibilities.

### Synchronous Workflow Orchestration

v0 does not use synchronous workflow orchestration across services.

Business workflows remain asynchronous and event-driven.

Synchronous capabilities may exist only for read-only queries governed by the Read-Only Query Model.

Business state progression must not depend on synchronous cross-service execution.

### Shared Business Databases

v0 explicitly excludes shared business databases.

Physical infrastructure may be shared.

Business ownership may not be shared.

A future implementation must not introduce shared tables or shared schemas that allow multiple services to modify the same business state.

## 12.3 Extension Points

The following components are explicitly designed for future extension.

Their existence does not imply immediate implementation.

### Agent Runtime

The Agent Runtime abstraction allows the execution layer to evolve independently from any specific AI vendor, model provider, or agent framework.

Future implementations may use different agent runtimes without changing the platform architecture.

### LLM Providers

The architecture is model-independent.

The platform may use different LLM providers over time.

Provider SDKs implement platform abstractions.

Provider SDKs do not define platform abstractions.

### Communication Providers

The Communication Service owns the communication provider abstraction.

Future providers may be added without changing workflow ownership or identity ownership.

### Identity Providers

The Identity Service owns identity provider integration.

Future identity systems must map to the canonical platform User model.

### Authentication Strategies

The External Events Service owns authentication strategy selection for inbound external events.

Future authentication strategies may be added without changing provider interpretation ownership.

### Scheduler Policies

The Scheduler may support new execution admission policies in future versions.

Scheduling policy evolution must preserve the separation between:

- workflow decision,
- scheduling decision,
- execution.

### Workflow Policies

Workflow behaviour is governed by policies.

New workflow policies may be added as the production lifecycle evolves.

No workflow transition may bypass policy evaluation.

### External Event Providers

New external providers may be added through the External Events Service and interpreted by the owning service.

External provider payloads must remain opaque to infrastructure.

Provider schemas must remain local to the owning service.

## 12.4 Architectural Stability

The architecture intentionally separates:

- business capabilities,
- infrastructure,
- implementation,
- vendor integrations,
- provider schemas,
- workflow policy,
- execution runtime.

Future implementation improvements should occur within these boundaries.

Architectural changes should remain exceptional.

A change is architectural when it modifies:

- service ownership,
- aggregate ownership,
- workflow authority,
- provider interpretation ownership,
- persistence ownership,
- contract categories,
- command or event semantics,
- execution authority,
- communication ownership,
- identity ownership.

A change is implementation-level when it modifies:

- libraries,
- SDKs,
- database technology,
- internal class structure,
- deployment mechanics,
- repository layout,
- local execution tooling,
- provider adapter internals,

without changing architectural ownership or platform contracts.

## 12.5 Evolution Process

The Software Factory Platform evolves according to the following order:

```text
Architecture
        ↓
Master Architecture Specification
        ↓
Architecture Validation Specification
        ↓
Implementation Decisions
        ↓
Implementation Blueprint
        ↓
Engineering Backlog
        ↓
Implementation
```

Every architectural evolution must begin by updating the Master Architecture Specification.

Derived artifacts are regenerated from it.

The Master Architecture Specification remains the single source of truth.

Architecture must not be changed implicitly during implementation.

If implementation discovers that the architecture is incomplete, inconsistent, or insufficient, the architecture must be revised first.

Only after the Master Architecture Specification is updated may derived artifacts be regenerated.

## 12.6 Version 0 Scope

Version 0.1.0 defines a complete, internally consistent architecture for the Software Factory Platform.

Implementation decisions intentionally remain outside the scope of this document unless they materially affect the architecture.

The objective of this specification is to define what the platform is, not how every internal component is implemented.

The following artifacts are expected to follow from this specification:

- Architecture Validation Specification,
- Implementation Decisions document,
- RFC-001,
- ADRs,
- Implementation Blueprint,
- Bootstrap Jira backlog,
- PlantUML diagrams,
- operational runbooks.

These artifacts are derived from this specification.

They are not authoritative when they contradict it.

## 12.7 Architecture Validation Specification

The architectural validation work is intentionally separated from the Master Architecture Specification.

The Master Architecture Specification defines the architecture.

The Architecture Validation Specification verifies the architecture through representative operational scenarios.

The Architecture Validation Specification should verify scenarios such as:

- end-to-end happy path,
- multiple PRSpecifications,
- coding failure,
- review failure,
- merge conflict,
- deployment failure,
- user approval,
- user notification,
- closed UserInteraction,
- duplicate message delivery,
- Workspace Worker crash,
- service restart,
- scheduler capacity limit,
- back-pressure handling,
- transient infrastructure failure.

The Architecture Validation Specification may fail scenarios.

If it discovers an architectural contradiction, the Master Architecture Specification must be updated.

The Architecture Validation Specification is derived from this document.

It is a separate artifact and is not embedded in the Master Architecture Specification.

## 12.8 Implementation Decisions

Implementation decisions are intentionally separated from architectural decisions.

Examples of implementation decisions include:

- Python framework selection,
- ORM selection,
- migration tooling,
- Agent Runtime implementation,
- Claude Code integration,
- GLM integration,
- Message Bus implementation details,
- Repository Manager implementation,
- Scheduler data structures,
- endpoint configuration persistence,
- local execution sandboxing,
- CI/CD mechanics.

Implementation decisions must not contradict the Master Architecture Specification.

Implementation decisions make the Blueprint deterministic.

Blueprint tickets should reference implementation decision identifiers where relevant.

## 12.9 Blueprint and Backlog Generation

The Implementation Blueprint is derived from:

- Master Architecture Specification,
- Implementation Decisions,
- Architecture Validation Specification findings where applicable.

The Blueprint must not introduce architecture.

The Blueprint translates architecture into implementation phases.

The Bootstrap Jira backlog is derived from the Blueprint.

Jira tickets must be deterministic.

A ticket should not require the assignee to make unresolved architectural decisions.

If a ticket requires an architectural decision, the Blueprint is incomplete.

If a ticket requires an implementation decision, the Implementation Decisions document is incomplete.

## 12.10 Final Architecture Statement

The Software Factory Platform architecture described in this specification is considered frozen for version 0.1.3.

Future evolution must occur through controlled architectural revisions.

Implementation is expected to conform to this specification.

The architecture is no longer governed by conversation.

It is governed by this document.

## 12.11 Chapter Status

This chapter is frozen for Architecture v0.

Future changes to non-goals, extension points, or the architecture evolution process require a new architectural revision.

# Appendix A — Glossary

## Aggregate

An Aggregate is a domain object that owns its lifecycle, invariants and state. Every Aggregate has exactly one owning service.

## Business Fact

A Business Fact is durable platform knowledge that records something meaningful that has happened in the domain. Events communicate business facts, but transport messages are not themselves authoritative business state.

## Business State

Business State is durable authoritative knowledge owned by a service. Examples include Ticket, PRSpecification, CodingJob, UserDecision and WorkflowDecision.

## Operational State

Operational State supports runtime execution and service behaviour. Examples include message ledgers, idempotency records, endpoint configuration, UserInteraction lifecycle state and Transactional Outbox entries.

## Derived State

Derived State is information computed from authoritative facts to improve usability, querying or operational efficiency. It must always be reproducible from authoritative sources.

## Command

A Command requests execution. Commands are point-to-point contracts with exactly one logical consumer.

## Event

An Event represents an immutable business fact. Events are publish-subscribe contracts.

## External Event

An External Event is an authenticated external provider payload wrapped by the External Events Service.

## External Schema

An External Schema is a service-local provider payload schema used by the service that owns interpretation of that provider. External Schemas are not platform contracts.

## Query

A Query retrieves read-only information owned by another service. Queries never modify business state or coordinate workflow progression.

## WorkflowDecision

A WorkflowDecision is the immutable audit record of a workflow decision, linking business facts, evaluated policy, emitted workflow-affecting commands and resulting workflow state.

## UserDecision

A UserDecision is the durable business outcome of a UserInteraction and belongs to the Orchestrator.

## Provider

A Provider is an external system integrated with the platform through service-owned abstractions.

## Transport Message

A Transport Message is the delivery representation of a Command, Event or External Event. It is not authoritative business state.

## Transactional Outbox

A Transactional Outbox is service-owned operational state that records outbound message intent atomically with business state changes and idempotency records, enabling reliable publication.
