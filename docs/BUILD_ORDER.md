# SFP — Build Order

Source: `docs/SFP_Ticket_Hierarchy.md`

Waves computed via longest-path (Kahn): `wave(t)=0` if no deps, else `1+max(wave(dep))`. Within each wave, tickets are sorted ascending by number.

`*(B→A)*` markers are informational (platform → manual-core) and are stripped for dependency resolution.

## Wave 0

| Ticket | Title | Area | Executor | Phase | Deps |
|---|---|---|---|---|---|
| SFP-1 | Create SFP GitHub monorepo | PREREQ | manual | Manual Core | — |
| SFP-2 | Obtain LLM provider API key (Anthropic-compatible) | PREREQ | manual | Manual Core | — |
| SFP-3 | Create SFP Jira project + API token | PREREQ | manual | Manual Core | — |
| SFP-4 | Configure local development environment | PREREQ | manual | Manual Core | — |
| SFP-64 | AWS account + billing (eu-west-1) | PREREQ | manual | Platform | — |

## Wave 1

| Ticket | Title | Area | Executor | Phase | Deps |
|---|---|---|---|---|---|
| SFP-5 | uv workspace + root pyproject + single uv.lock | WORKSPACE | ai-agent | Manual Core | SFP-1, SFP-4 |
| SFP-65 | IAM user for Pulumi | PREREQ | manual | Platform | SFP-64 |
| SFP-67 | Integration secrets in Secrets Manager | PREREQ | manual | Platform | SFP-64 |
| SFP-68 | Domain/DNS | PREREQ | manual | Platform | SFP-64 |
| SFP-69 | Slack workspace + app credentials | PREREQ | manual | Platform | SFP-64 |
| SFP-171 | Create the `sfp-reviewer-bot` GitHub account + token | PREREQ | manual | Platform | SFP-1 |

## Wave 2

| Ticket | Title | Area | Executor | Phase | Deps |
|---|---|---|---|---|---|
| SFP-6 | Monorepo skeleton: services/*, packages/* | WORKSPACE | ai-agent | Manual Core | SFP-5 |
| SFP-8 | ruff configuration (lint + format) | WORKSPACE | ai-agent | Manual Core | SFP-5 |
| SFP-9 | mypy configuration | WORKSPACE | ai-agent | Manual Core | SFP-5 |
| SFP-10 | pytest + pytest-asyncio + coverage (90% gate) | WORKSPACE | ai-agent | Manual Core | SFP-5 |
| SFP-66 | Pulumi bootstrap (state backend + stack config) | PREREQ | manual | Platform | SFP-65 |
| SFP-78 | Secrets Manager + config injection wiring | PLAT-INFRA | ai-agent | Platform | SFP-67 |

## Wave 3

| Ticket | Title | Area | Executor | Phase | Deps |
|---|---|---|---|---|---|
| SFP-7 | Uniform per-service layout template | WORKSPACE | ai-agent | Manual Core | SFP-6 |
| SFP-11 | sfp-config: typed config + env loading | WORKSPACE | ai-agent | Manual Core | SFP-6 |
| SFP-13 | Common agent output envelope schema | CONTRACTS | ai-agent | Manual Core | SFP-6 |
| SFP-19 | Context-types catalogue (shared, versioned) | CONTRACTS | ai-agent | Manual Core | SFP-6 |
| SFP-21 | Command contracts catalogue | CONTRACTS | ai-agent | Manual Core | SFP-6 |
| SFP-22 | Event contracts catalogue | CONTRACTS | ai-agent | Manual Core | SFP-6 |
| SFP-23 | ExternalEventReceived contract | CONTRACTS | ai-agent | Manual Core | SFP-6 |
| SFP-24 | Validation-profile enum + gate-mapping | CONTRACTS | ai-agent | Manual Core | SFP-6 |
| SFP-38 | Repository Manager: clone | AGENT | ai-agent | Manual Core | SFP-6 |
| SFP-45 | Local Execution Engine: build | AGENT | ai-agent | Manual Core | SFP-6 |
| SFP-59 | docker compose: Postgres (multi-DB) | LOCAL | ai-agent | Manual Core | SFP-6 |
| SFP-70 | VPC + subnets | PLAT-INFRA | ai-agent | Platform | SFP-66 |
| SFP-94 | Identity DB: Base + `User` + `UserExternalIdentity` | PERSIST | ai-agent | Platform | SFP-6 |
| SFP-95 | Communication DB: `UserInteraction` | PERSIST | ai-agent | Platform | SFP-6 |
| SFP-96 | External Events DB: endpoint config | PERSIST | ai-agent | Platform | SFP-6 |
| SFP-97 | Pulumi: SNS topics | MSG-INFRA | ai-agent | Platform | SFP-66 |
| SFP-98 | Pulumi: SQS queues | MSG-INFRA | ai-agent | Platform | SFP-66 |
| SFP-155 | ci workflow: lint (ruff) | CICD | ai-agent | Platform | SFP-8 |
| SFP-156 | ci workflow: typecheck (mypy) | CICD | ai-agent | Platform | SFP-9 |
| SFP-157 | ci workflow: test + coverage gate | CICD | ai-agent | Platform | SFP-10 |
| SFP-161 | infrastructure workflow: pulumi preview/up | CICD | ai-agent | Platform | SFP-66 |

## Wave 4

| Ticket | Title | Area | Executor | Phase | Deps |
|---|---|---|---|---|---|
| SFP-12 | sfp-config: local secret provider (env/file) | WORKSPACE | ai-agent | Manual Core | SFP-11 |
| SFP-14 | Planner output schema (`pr_specs`) | CONTRACTS | ai-agent | Manual Core | SFP-13 |
| SFP-15 | Coder output schema | CONTRACTS | ai-agent | Manual Core | SFP-13 |
| SFP-16 | Reviewer output schema (judgment-only) | CONTRACTS | ai-agent | Manual Core | SFP-13 |
| SFP-17 | Test Designer output schema | CONTRACTS | ai-agent | Manual Core | SFP-13 |
| SFP-18 | Readiness evaluator output schema | CONTRACTS | ai-agent | Manual Core | SFP-13 |
| SFP-20 | Ticket context I/O schemas (outputs/inputs) | CONTRACTS | ai-agent | Manual Core | SFP-19 |
| SFP-25 | sfp-messaging: Message Bus interface | SHARED-FW | ai-agent | Manual Core | SFP-21, SFP-22, SFP-23, SFP-11 |
| SFP-30 | sfp-observability: structlog setup + JSON | SHARED-FW | ai-agent | Manual Core | SFP-11 |
| SFP-34 | sfp-agent-runtime: abstraction interfaces | SHARED-FW | ai-agent | Manual Core | SFP-11 |
| SFP-39 | Repository Manager: worktree lifecycle | AGENT | ai-agent | Manual Core | SFP-38 |
| SFP-40 | Repository Manager: branch lifecycle + cleanup | AGENT | ai-agent | Manual Core | SFP-38 |
| SFP-41 | Git Provider Adapter: branch + push | AGENT | ai-agent | Manual Core | SFP-11 |
| SFP-46 | Local Execution Engine: test runner | AGENT | ai-agent | Manual Core | SFP-45 |
| SFP-47 | Local Execution Engine: linters/static analysis | AGENT | ai-agent | Manual Core | SFP-45 |
| SFP-48 | Sandbox: local container isolation | AGENT | ai-agent | Manual Core | SFP-45 |
| SFP-50 | Readiness gate: rubric (rule-checks) | AGENT | ai-agent | Manual Core | SFP-24 |
| SFP-57 | Validation profile logic: assignment + gate enforcement | AGENT | ai-agent | Manual Core | SFP-24 |
| SFP-58 | Failure classification logic | AGENT | ai-agent | Manual Core | SFP-13 |
| SFP-60 | docker compose: LocalStack (SNS/SQS/DLQ) | LOCAL | ai-agent | Manual Core | SFP-59 |
| SFP-61 | docker compose: OTel collector (dev sink) | LOCAL | ai-agent | Manual Core | SFP-59 |
| SFP-71 | NAT gateway + routing | PLAT-INFRA | ai-agent | Platform | SFP-70 |
| SFP-72 | Security groups | PLAT-INFRA | ai-agent | Platform | SFP-70 |
| SFP-73 | Aurora Serverless PostgreSQL (multi-DB) | PLAT-INFRA | ai-agent | Platform | SFP-70, SFP-67 |
| SFP-74 | ECS cluster (Fargate) | PLAT-INFRA | ai-agent | Platform | SFP-70 |
| SFP-77 | IAM roles + policies (per service) | PLAT-INFRA | ai-agent | Platform | SFP-70 |
| SFP-79 | CloudWatch Logs groups + shipping | PLAT-INFRA | ai-agent | Platform | SFP-70 |
| SFP-81 | DNS + TLS (Route53 + ACM) | PLAT-INFRA | ai-agent | Platform | SFP-68, SFP-70 |
| SFP-82 | Orchestrator DB: Alembic + Base + business/operational schemas | PERSIST | ai-agent | Platform | SFP-6, SFP-13 |
| SFP-99 | Pulumi: DLQs + redrive policies | MSG-INFRA | ai-agent | Platform | SFP-98 |
| SFP-100 | Pulumi: SNS→SQS subscriptions | MSG-INFRA | ai-agent | Platform | SFP-97, SFP-98 |
| SFP-162 | Reusable workflow templates (workflow_call) | CICD | ai-agent | Platform | SFP-155 |
| SFP-165 | Back-pressure → queue absorbs | VAL | ai-agent | Platform | SFP-98 |

## Wave 5

| Ticket | Title | Area | Executor | Phase | Deps |
|---|---|---|---|---|---|
| SFP-26 | sfp-messaging: Handler + decorators | SHARED-FW | ai-agent | Manual Core | SFP-25 |
| SFP-27 | sfp-messaging: MessageContext | SHARED-FW | ai-agent | Manual Core | SFP-25 |
| SFP-28 | sfp-messaging: envelope serde (JSON) | SHARED-FW | ai-agent | Manual Core | SFP-13, SFP-25 |
| SFP-29 | sfp-messaging: in-memory transport | SHARED-FW | ai-agent | Manual Core | SFP-25 |
| SFP-31 | sfp-observability: correlation/causation binding | SHARED-FW | ai-agent | Manual Core | SFP-30 |
| SFP-35 | sfp-agent-runtime: PromptBuilder | SHARED-FW | ai-agent | Manual Core | SFP-34 |
| SFP-36 | Agent Runtime impl (Claude Agent SDK wrapper) | AGENT | ai-agent | Manual Core | SFP-34, SFP-11 |
| SFP-42 | Git Provider Adapter: PR create/update | AGENT | ai-agent | Manual Core | SFP-41 |
| SFP-43 | Git Provider Adapter: review submission | AGENT | ai-agent | Manual Core | SFP-41, SFP-171 |
| SFP-44 | Git Provider Adapter: branch synchronization | AGENT | ai-agent | Manual Core | SFP-41 |
| SFP-49 | Ticket context resolver: input resolution + injection | AGENT | ai-agent | Manual Core | SFP-20, SFP-19 |
| SFP-62 | Compose init: local provisioning + Alembic baseline | LOCAL | ai-agent | Manual Core | SFP-60 |
| SFP-75 | ECR registries (per service) | PLAT-INFRA | ai-agent | Platform | SFP-74 |
| SFP-76 | AWS Batch compute env + job queue + max-vCpus | PLAT-INFRA | ai-agent | Platform | SFP-74 |
| SFP-80 | Alarms + dashboards | PLAT-INFRA | ai-agent | Platform | SFP-79 |
| SFP-83 | `Project` + `ProjectUser` models | PERSIST | ai-agent | Platform | SFP-82 |
| SFP-84 | `Ticket` model | PERSIST | ai-agent | Platform | SFP-82 |
| SFP-85 | `PRSpecification` model | PERSIST | ai-agent | Platform | SFP-82 |
| SFP-86 | `CodingJob` model | PERSIST | ai-agent | Platform | SFP-82 |
| SFP-87 | `Review` model | PERSIST | ai-agent | Platform | SFP-82 |
| SFP-88 | `Merge` model | PERSIST | ai-agent | Platform | SFP-82 |
| SFP-89 | `Deployment` model | PERSIST | ai-agent | Platform | SFP-82 |
| SFP-90 | `UserDecision` model | PERSIST | ai-agent | Platform | SFP-82 |
| SFP-91 | `WorkflowDecision` model | PERSIST | ai-agent | Platform | SFP-82 |
| SFP-92 | Transactional outbox table + relay publisher | PERSIST | ai-agent | Platform | SFP-82 |
| SFP-93 | Idempotency / message ledger | PERSIST | ai-agent | Platform | SFP-82 |
| SFP-158 | ci workflow: integration tests (LocalStack) | CICD | ai-agent | Platform | SFP-60, SFP-157 |

## Wave 6

| Ticket | Title | Area | Executor | Phase | Deps |
|---|---|---|---|---|---|
| SFP-32 | sfp-testing: in-memory bus fake | SHARED-FW | ai-agent | Manual Core | SFP-29 |
| SFP-33 | sfp-testing: fake MessageContext + fixtures | SHARED-FW | ai-agent | Manual Core | SFP-27 |
| SFP-37 | Per-role model config routing | AGENT | ai-agent | Manual Core | SFP-36, SFP-11 |
| SFP-51 | Readiness gate: evaluator + verdicts | AGENT | ai-agent | Manual Core | SFP-18, SFP-49, SFP-50 |
| SFP-53 | Planner agent + prompt | AGENT | ai-agent | Manual Core | SFP-36, SFP-14, SFP-35, SFP-24 |
| SFP-54 | Test Designer agent + prompt | AGENT | ai-agent | Manual Core | SFP-36, SFP-17, SFP-35 |
| SFP-55 | Coder agent + prompt | AGENT | ai-agent | Manual Core | SFP-36, SFP-15, SFP-35, SFP-38, SFP-41, SFP-45 |
| SFP-56 | Reviewer agent + prompt (judgment-only) | AGENT | ai-agent | Manual Core | SFP-36, SFP-16, SFP-35, SFP-42, SFP-171 |
| SFP-101 | sfp-messaging SNS/SQS transport | MSG-INFRA | ai-agent | Platform | SFP-29, SFP-97, SFP-98 |
| SFP-150 | Batch task definition (container, sandbox, egress policy) | WSW | ai-agent | Platform | SFP-76, SFP-48 |
| SFP-159 | release workflow: build + push (ECR) | CICD | ai-agent | Platform | SFP-75 |
| SFP-164 | Crash mid-outbox → no duplicate business effect | VAL | ai-agent | Platform | SFP-92 |

## Wave 7

| Ticket | Title | Area | Executor | Phase | Deps |
|---|---|---|---|---|---|
| SFP-52 | Readiness gate: manual-required classification | AGENT | ai-agent | Manual Core | SFP-51 |
| SFP-63 | Manual-run runbook | LOCAL | ai-agent | Manual Core | SFP-49, SFP-51, SFP-53, SFP-54, SFP-55, SFP-56, SFP-57 |
| SFP-102 | Envelope propagation across SNS/SQS | MSG-INFRA | ai-agent | Platform | SFP-28, SFP-101 |
| SFP-103 | Webhook endpoint `/webhooks/{endpoint_id}` | EXT-EVT | ai-agent | Platform | SFP-96, SFP-101 |
| SFP-108 | User aggregate + lifecycle | IDENT | ai-agent | Platform | SFP-94, SFP-101 |
| SFP-112 | UserInteraction lifecycle (create/expire/complete) | COMM | ai-agent | Platform | SFP-95, SFP-101 |
| SFP-120 | Workflow engine + state machine | ORCH | ai-agent | Platform | SFP-82, SFP-101 |
| SFP-143 | Execution coordinator service skeleton | WSW | ai-agent | Platform | SFP-101, SFP-36, SFP-55, SFP-56 |
| SFP-160 | release workflow: deploy (Pulumi + ECS) | CICD | ai-agent | Platform | SFP-159 |
| SFP-163 | Duplicate delivery → idempotency holds | VAL | ai-agent | Platform | SFP-101, SFP-93 |

## Wave 8

| Ticket | Title | Area | Executor | Phase | Deps |
|---|---|---|---|---|---|
| SFP-104 | Endpoint config resolver | EXT-EVT | ai-agent | Platform | SFP-103 |
| SFP-107 | ExternalEventReceived publisher | EXT-EVT | ai-agent | Platform | SFP-103, SFP-101 |
| SFP-109 | UserExternalIdentity + mappings | IDENT | ai-agent | Platform | SFP-108 |
| SFP-113 | UserInteraction summary + expiration timer | COMM | ai-agent | Platform | SFP-112 |
| SFP-114 | Closed-interaction handling (no reopen; context ref) | COMM | ai-agent | Platform | SFP-112 |
| SFP-116 | Slack provider: outbound messages | COMM | ai-agent | Platform | SFP-112 |
| SFP-117 | Communication Agent (summarization, context) | COMM | ai-agent | Platform | SFP-112, SFP-36 |
| SFP-121 | Transitions: ticket→PR-spec stage | ORCH | ai-agent | Platform | SFP-120 |
| SFP-122 | Transitions: coding/review stages | ORCH | ai-agent | Platform | SFP-120 |
| SFP-123 | Transitions: merge/deploy stages | ORCH | ai-agent | Platform | SFP-120 |
| SFP-124 | Transitions: WAITING_FOR_USER + failure/terminal | ORCH | ai-agent | Platform | SFP-120 |
| SFP-125 | Policy engine (deterministic evaluation) | ORCH | ai-agent | Platform | SFP-120 |
| SFP-130 | Aggregate manager (consistency) | ORCH | ai-agent | Platform | SFP-120 |
| SFP-132 | Hosting: Readiness Gate wiring | ORCH | ai-agent | Platform | SFP-51, SFP-120 |
| SFP-133 | Hosting: Planner wiring | ORCH | ai-agent | Platform | SFP-53, SFP-120 |
| SFP-134 | Hosting: context resolver wiring | ORCH | ai-agent | Platform | SFP-49, SFP-120 |
| SFP-135 | Command emission: ExecuteCodingJob | ORCH | ai-agent | Platform | SFP-120, SFP-101 |
| SFP-136 | Command emission: ReviewPullRequest | ORCH | ai-agent | Platform | SFP-120 |
| SFP-137 | Command emission: SynchronizePullRequest | ORCH | ai-agent | Platform | SFP-120 |
| SFP-138 | Command emission: RequestMerge | ORCH | ai-agent | Platform | SFP-120 |
| SFP-139 | Command emission: RequestUserInput + NotifyUser | ORCH | ai-agent | Platform | SFP-120 |
| SFP-140 | Command emission: CancelCodingJob + CancelReviewJob | ORCH | ai-agent | Platform | SFP-120 |
| SFP-141 | Query API: RetrieveWorkflowContext | ORCH | ai-agent | Platform | SFP-120 |
| SFP-142 | Query API: RetrieveTicketSummary + RetrieveProject | ORCH | ai-agent | Platform | SFP-120 |
| SFP-144 | Consumer: ExecuteCodingJob | WSW | ai-agent | Platform | SFP-143, SFP-101, SFP-76 |
| SFP-145 | Consumer: ReviewPullRequest | WSW | ai-agent | Platform | SFP-143 |
| SFP-146 | Consumer: SynchronizePullRequest | WSW | ai-agent | Platform | SFP-143 |
| SFP-147 | Consumer: RequestMerge | WSW | ai-agent | Platform | SFP-143 |
| SFP-148 | Consumer: CancelCodingJob + CancelReviewJob | WSW | ai-agent | Platform | SFP-143 |
| SFP-151 | Execution reporting: CodingJobUpdated | WSW | ai-agent | Platform | SFP-143 |
| SFP-152 | Execution reporting: ReviewUpdated + MergeUpdated | WSW | ai-agent | Platform | SFP-143 |
| SFP-154 | Workspace Worker observability hooks | WSW | ai-agent | Platform | SFP-143 |
| SFP-168 | Closed UserInteraction not reopened | VAL | ai-agent | Platform | SFP-112 |

## Wave 9

| Ticket | Title | Area | Executor | Phase | Deps |
|---|---|---|---|---|---|
| SFP-105 | Authentication strategy factory | EXT-EVT | ai-agent | Platform | SFP-104 |
| SFP-110 | Identity resolution (external↔platform) | IDENT | ai-agent | Platform | SFP-108, SFP-109 |
| SFP-115 | Slack provider: inbound event handling | COMM | ai-agent | Platform | SFP-107, SFP-112 |
| SFP-118 | CONFIRM flow (summary→CONFIRM→UserDecision) | COMM | ai-agent | Platform | SFP-112, SFP-117 |
| SFP-119 | Notifications + input requests (NotifyUser/RequestUserInput) | COMM | ai-agent | Platform | SFP-116 |
| SFP-126 | Policies: coding-start / review-success / merge-ready | ORCH | ai-agent | Platform | SFP-125 |
| SFP-127 | Policies: user-approval / deploy-begin / fail | ORCH | ai-agent | Platform | SFP-125 |
| SFP-128 | Scheduler: admission + concurrency | ORCH | ai-agent | Platform | SFP-125, SFP-76 |
| SFP-131 | Decision recorder (WorkflowDecision persistence) | ORCH | ai-agent | Platform | SFP-130 |
| SFP-149 | SQS→Batch bridge + task-per-job dispatch | WSW | ai-agent | Platform | SFP-144, SFP-76 |
| SFP-153 | Merge execution via Git Provider Adapter | WSW | ai-agent | Platform | SFP-147, SFP-44 |
| SFP-170 | E2E happy path: ticket→plan→code→review→merge→deploy | VAL | ai-agent | Platform | SFP-132, SFP-143 |

## Wave 10

| Ticket | Title | Area | Executor | Phase | Deps |
|---|---|---|---|---|---|
| SFP-106 | Authentication strategies (HMAC, signatures) | EXT-EVT | ai-agent | Platform | SFP-105 |
| SFP-111 | Identity read-only query API | IDENT | ai-agent | Platform | SFP-110 |
| SFP-129 | Scheduler: max-vCpus ceiling + dispatch | ORCH | ai-agent | Platform | SFP-128 |
| SFP-166 | Worker crash/restart → recover from durable state | VAL | ai-agent | Platform | SFP-143, SFP-131 |
| SFP-167 | Scheduler capacity/admission enforced | VAL | ai-agent | Platform | SFP-128 |
| SFP-169 | Merge conflict handling | VAL | ai-agent | Platform | SFP-153 |

