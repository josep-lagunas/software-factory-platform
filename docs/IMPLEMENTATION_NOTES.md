# SFP Implementation Notes

## Purpose

This document captures implementation-level decisions that complement the Master Architecture Specification. It focuses on engineering conventions and implementation guidance rather than architecture.

---

# 1. Decorator-Based Messaging Framework

## Goal

The primary objective of the messaging framework is to completely hide transport mechanics from business code.

Business code should not know whether messages arrive through SNS, SQS, another broker, or an in-memory implementation used for testing.

Handlers should express only:

- what they consume,
- what business logic they execute,
- what commands/events they emit.

Everything else belongs to the shared framework.

## Design Philosophy

The messaging framework is an implementation of the Message Bus abstraction defined in the Master Architecture Specification.

Business handlers must never interact directly with:

- boto3
- SNS Topics
- SQS Queues
- Queue URLs
- Topic ARNs
- ReceiveMessage
- DeleteMessage
- VisibilityTimeout
- MessageAttributes
- Raw JSON envelopes

These concepts belong exclusively to the infrastructure layer.

The framework translates infrastructure concerns into a clean programming model.

## Handler Declaration

Handlers should be declared declaratively.

Conceptually:

```python
@command_handler(ExecuteCodingJob)
async def handle(command: ExecuteCodingJob, context: MessageContext):
    ...
```

or

```python
@event_handler(UserInputReceived)
async def handle(event: UserInputReceived, context: MessageContext):
    ...
```

The decorator registers the handler automatically.

The developer never manually subscribes queues or topics.

## Responsibilities of the Framework

The messaging framework owns:

- handler discovery and registration,
- mapping message types to handlers,
- SNS subscription wiring,
- SQS polling,
- envelope deserialization,
- Pydantic validation,
- context creation,
- correlation propagation,
- causation propagation,
- idempotency checks,
- retry classification,
- DLQ integration,
- acknowledgement,
- logging,
- tracing,
- metrics,
- timing,
- exception mapping,
- publishing resulting commands/events.

## MessageContext

Every handler receives a framework-provided `MessageContext`.

It should expose information such as:

- correlation_id,
- causation_id,
- message_id,
- received_at,
- retry_count,
- trace/span references,
- framework services.

Handlers should never reconstruct this information themselves.

## Publishing Messages

Handlers should publish new commands and events through the Message Bus abstraction.

They should never instantiate SNS clients or publish directly to AWS.

Conceptually:

```python
await message_bus.publish(CodingJobUpdated(...))
```

The framework resolves routing and transport.

## Testing

The framework must provide testing helpers.

Unit tests should be able to execute handlers without:

- AWS,
- SNS,
- SQS,
- LocalStack.

Fake MessageBus implementations and fake MessageContexts should be available.

## What Decorators Must Never Do

Decorators must never:

- evaluate workflow policies,
- change aggregate state,
- interpret provider payload semantics,
- make business decisions,
- schedule work,
- communicate with users,
- hide business behaviour.

Their responsibility ends where business logic begins.

## Guiding Principle

The framework hides infrastructure.

The application layer expresses business behaviour.

Infrastructure concerns should disappear from business code, but business behaviour should remain explicit and readable.

---

# 2. Monorepo

## Context

The Software Factory Platform (SFP) codebase consists of multiple services, shared libraries, infrastructure definitions, and documentation. Managing these components efficiently requires a source control strategy that facilitates collaboration, dependency management, and consistency.

## Decision

We have chosen to adopt a monorepository (monorepo) approach to organize the entire platform's codebase. All services, shared packages, infrastructure code, scripts, and documentation reside within a single repository.

## Rationale

- **Simplified dependency management:** Shared packages and common dependencies can be updated and versioned in lockstep with services that consume them.
- **Consistent tooling and configuration:** Build, test, and deployment pipelines can be standardized across all components.
- **Atomic changes:** Cross-cutting changes affecting multiple services or packages can be made in a single commit, reducing integration issues.
- **Improved discoverability:** Developers have visibility into all platform components, easing onboarding and collaboration.
- **Simplified refactoring:** Refactoring shared code or interfaces is easier when all consumers are in the same repository.

## Alternatives Considered

- **Repository-per-service:** Each service and package in its own repository, providing strict separation. This increases overhead in managing cross-repository changes, dependency versioning, and tooling consistency.
- **Monolith repository:** A single deployable application containing all services. This conflicts with the requirement for independently deployable and owned services.
- **Separate documentation repository:** Keeping docs outside the codebase improves separation but complicates synchronization and discoverability.

## Consequences

- **Positive:**
  - Easier cross-service coordination and refactoring.
  - Simplified dependency and version management.
  - Unified CI/CD pipelines.
  - Easier discovery and contribution across teams.

- **Negative:**
  - Larger repository size may impact clone and build times.
  - Requires tooling support for partial builds and tests.
  - Potential for increased merge conflicts in shared areas.
  - Risk of tighter coupling if boundaries are not enforced.

## Guiding Principle

The monorepo is a source control and organizational decision, not an architectural one.

Services remain independently deployable and independently owned.

The monorepo enables engineering efficiency while preserving service autonomy.

---

# 3. Uniform Service Layout

Every service follows the same internal organization to promote consistency, maintainability, and clear separation of concerns.

```text
src/
    domain/
    application/
    interfaces/
    infrastructure/
    entrypoints/
```

## domain/

**Responsibilities:**  
- Define aggregates, entities, value objects, domain events, invariants, and core business rules.  
- Encapsulate business logic and domain state transitions independent of infrastructure or application concerns.

**Allowed Dependencies:**  
- Standard library  
- Domain primitives and shared kernel packages (e.g., `sfp-contracts`)

**Forbidden Responsibilities:**  
- No I/O or infrastructure code (e.g., database, messaging).  
- No application service orchestration or handler logic.  
- No HTTP or message adapter code.

**Examples:**  
- `Order` aggregate root with methods enforcing invariants.  
- `Money` value object with currency rules.  
- Domain exceptions and domain events.

## application/

**Responsibilities:**  
- Implement command handlers, event handlers, query handlers, and use cases.  
- Coordinate domain objects and infrastructure services to fulfill application workflows.  
- Enforce application-level policies and transaction boundaries.

**Allowed Dependencies:**  
- `domain/`  
- Infrastructure abstractions (e.g., repositories, message bus interfaces).  
- Shared packages for validation, logging, and configuration.

**Forbidden Responsibilities:**  
- Direct infrastructure implementation details (e.g., SNS client code).  
- HTTP routing or transport concerns.

**Examples:**  
- `ExecuteOrderCommandHandler` that loads an `Order` aggregate, executes a command, and saves changes.  
- Query handlers that fetch data via repositories.  
- Application services coordinating multiple aggregates.

## interfaces/

**Responsibilities:**  
- Expose provider-facing interfaces such as HTTP routes, message adapters, GraphQL resolvers, or CLI commands.  
- Translate external payloads into internal commands/events and vice versa.  
- Handle protocol-specific concerns like serialization, validation, and authentication.

**Allowed Dependencies:**  
- `application/`  
- Shared packages for serialization and validation.

**Forbidden Responsibilities:**  
- Business logic or domain rules.  
- Infrastructure implementation beyond adapter code.

**Examples:**  
- FastAPI route definitions translating HTTP requests into commands.  
- SNS/SQS message adapters converting raw messages into domain events.  
- Webhook receivers.

## infrastructure/

**Responsibilities:**  
- Implement concrete infrastructure services such as repositories, SNS/SQS clients, database access, configuration loading, and provider adapters.  
- Encapsulate all I/O and external system integration.

**Allowed Dependencies:**  
- `application/` interfaces or abstractions.  
- External SDKs and libraries.

**Forbidden Responsibilities:**  
- Business logic or application orchestration.  
- HTTP route definitions.

**Examples:**  
- DynamoDB repository implementations.  
- SNS/SQS client wrappers.  
- Configuration providers reading from environment variables or secrets manager.

## entrypoints/

**Responsibilities:**  
- Bootstrap the service runtime environment.  
- Wire dependencies and configure frameworks (e.g., FastAPI startup, worker initialization).  
- Define the main executable entrypoints.

**Allowed Dependencies:**  
- All internal layers (`domain/`, `application/`, `interfaces/`, `infrastructure/`).  
- Framework libraries.

**Forbidden Responsibilities:**  
- Business logic or domain code.  
- Direct infrastructure implementation beyond wiring.

**Examples:**  
- FastAPI app startup script.  
- Worker process bootstrap code.  
- Dependency injection container configuration.

## Design Principles

- **Dependency Direction:** Dependencies flow inward, from `entrypoints/` to `domain/`. `domain/` is the core with no dependencies on other layers.  
- **Uniformity:** All services use the same layout to reduce cognitive load, enable tooling support, and facilitate cross-team collaboration.  
- **Separation of Concerns:** Each folder has a clear responsibility boundary, reducing coupling and improving maintainability.

---

# 4. Shared Packages

The platform provides reusable packages to encapsulate common concerns and promote code reuse.

## sfp-contracts

**Purpose:**  
Define shared data contracts, schemas, and message definitions used across services.

**Responsibilities:**  
- Pydantic models for commands, events, and queries.  
- Common domain primitives and types.

**Examples:**  
- `UserCreated` event schema.  
- `ExecuteCodingJob` command model.

**Does Not Include:**  
- Provider-specific schemas (Slack, Jira, GitHub, etc.) which remain inside the owning service.  
- Business logic or handlers.

## sfp-messaging

**Purpose:**  
Implement the messaging framework abstraction including decorators, message bus interfaces, and transport-agnostic messaging primitives.

**Responsibilities:**  
- Decorators for command and event handlers.  
- MessageContext definition.  
- Message bus interfaces and test doubles.

**Examples:**  
- `@command_handler` decorator.  
- `MessageBus` interface.  
- In-memory message bus for testing.

**Does Not Include:**  
- Concrete SNS/SQS client implementations (which live in service infrastructure).  
- Business logic.

## sfp-observability

**Purpose:**  
Provide observability tools such as logging, tracing, metrics, and timing utilities.

**Responsibilities:**  
- Structured logging utilities.  
- OpenTelemetry tracing wrappers.  
- Metrics collection interfaces.

**Examples:**  
- Logger adapters.  
- Span creation helpers.  
- Metrics counters and histograms.

**Does Not Include:**  
- Service-specific metrics or dashboards.  
- Infrastructure provisioning code.

## sfp-config

**Purpose:**  
Centralize configuration management and loading.

**Responsibilities:**  
- Environment variable parsing.  
- Typed configuration objects.  
- Secrets management abstractions.

**Examples:**  
- Config classes for database connection strings.  
- Helpers to load config from environment or files.

**Does Not Include:**  
- Infrastructure provisioning or secrets store implementations.

## sfp-auth

**Purpose:**  
Implement authentication and authorization primitives.

**Responsibilities:**  
- Token validation.  
- User identity abstractions.  
- Permission checks.

**Examples:**  
- JWT token parsers.  
- Role-based access control helpers.

**Does Not Include:**  
- Provider-specific auth integrations (e.g., OAuth providers).

## sfp-agent-runtime

**Purpose:**  
Provide runtime support for agent-based workflows and long-running processes.

**Responsibilities:**  
- Agent lifecycle management.  
- Workflow orchestration primitives.

**Examples:**  
- Agent state machines.  
- Workflow step definitions.

**Does Not Include:**  
- Business logic or domain-specific workflows.

## sfp-testing

**Purpose:**  
Offer testing utilities and fakes for unit and integration tests.

**Responsibilities:**  
- Fake message bus implementations.  
- Mock context providers.  
- Test fixtures and helpers.

**Examples:**  
- In-memory message bus for handler tests.  
- Context factories.

**Does Not Include:**  
- End-to-end test frameworks or infrastructure.

---

# 5. Infrastructure Layout

Infrastructure code is organized to clearly separate shared platform resources from service-specific and local development infrastructure.

```text
infrastructure/
├── platform/
├── services/
└── local/
```

## Platform Infrastructure

**Scope:**  
Shared physical infrastructure resources owned by the platform team.

**Responsibilities:**  
- Centralized logging and monitoring infrastructure.  
- Shared networking components (VPCs, subnets).  
- Common IAM roles and policies.  
- Shared databases or caches used by multiple services.  
- Global DNS and certificate management.

**Examples:**  
- CloudWatch log groups for all services.  
- Centralized S3 buckets for artifacts.  
- IAM roles for platform-wide access.

## Service Infrastructure

**Scope:**  
Infrastructure owned and managed by individual services.

**Responsibilities:**  
- Service-specific queues (SNS topics, SQS queues).  
- Compute resources (Lambda functions, ECS tasks).  
- Service-specific IAM policies.  
- Alarms and alerts scoped to the service.  
- Secrets and configuration specific to the service.

**Examples:**  
- SQS queue for `order-service` commands.  
- Lambda function deployment for `payment-service`.  
- IAM roles granting access only to service resources.

## Local Infrastructure

**Scope:**  
Infrastructure and tooling supporting local development and testing.

**Responsibilities:**  
- LocalStack configurations.  
- Docker Compose files for local dependencies.  
- Mock or stub infrastructure components.

**Examples:**  
- Local SNS/SQS emulators.  
- Local database containers.  
- Scripts to bootstrap local environment.

## Ownership Rules

- Platform infrastructure is owned and maintained by the platform team to ensure consistency and shared resource management.  
- Service infrastructure is owned by the respective service teams, enabling autonomy and independent deployment.  
- Local infrastructure is maintained by individual developers or teams to facilitate efficient development workflows.

## Design Principles

- **Clear Separation:** Distinct boundaries between shared and service-specific infrastructure reduce risk and improve clarity.  
- **Autonomy:** Service teams control their own infrastructure within the constraints of platform-wide policies.  
- **Scalability:** Shared infrastructure supports scale and cross-service concerns without coupling services unnecessarily.  
- **Developer Experience:** Local infrastructure enables rapid iteration without dependence on cloud resources.

---

# Notes

These are implementation conventions extracted from the architecture work. They should later be converted into formal implementation decisions (ADRs or IMPLEMENTATION_DECISIONS entries) together with additional implementation topics such as persistence, testing, observability and CI/CD.