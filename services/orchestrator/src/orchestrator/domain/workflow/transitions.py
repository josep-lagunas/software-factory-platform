"""Stage-transition drivers for the coding/review/merge stages (MAS §8.4–8.8).

Thin, **pure** drivers that advance a ticket workflow through the coding,
review, and merge/deploy stages, delegating every actual move to the SFP-137
engine (:func:`orchestrator.domain.workflow.state_machine.transition`), which
validates against :data:`TRANSITIONS` and returns the immutable
:class:`WorkflowDecision` (§8.5). The driven edges are:

- ``READY_FOR_PR_SPECIFICATION → READY_FOR_CODING`` (SFP-138) — fired by a
  *successful-plan* fact (the planner ran to ``SUCCESS`` with ≥1 PR-spec).
- ``READY_FOR_CODING → CODING_IN_PROGRESS`` (SFP-139) — fired by a *coding job
  started* fact (a ``CodingJobUpdated``-style observation whose job status is
  ``running``).
- ``CODING_IN_PROGRESS → REVIEW_IN_PROGRESS`` (SFP-139) — fired by a *PR created
  / review requested* fact (the Coder's branch/PR opened for review).
- ``REVIEW_IN_PROGRESS → CODING_IN_PROGRESS`` (SFP-139, the ID-068 rework loop)
  — fired by a *changes-requested review* fact (a ``ReviewUpdated``-style
  observation whose ``review_status`` is ``CHANGES_REQUESTED``). Rework is
  **normal workflow progression**, never a failure and never an escalation.
- ``READY_FOR_MERGE → MERGING`` (SFP-140) — fired by an *approval* fact (an
  ``approved`` review verdict for the PR).
- ``MERGING → DEPLOYING`` and ``DEPLOYING → COMPLETED`` (SFP-140) — fired by a
  *merge completed + deploy target present* fact and a *succeeded deployment*
  fact respectively. A ``failed`` deployment is **not** this driver's move:
  failure handling belongs to the landed SFP-144 ``ShouldFailPolicy``, so a
  failed deployment is recorded as a no-move here and the workflow is never
  routed into ``FAILED`` by this module.
- ``MERGING → WAITING_FOR_USER`` (SFP-140, the ID-024 parking edge) — fired by
  an *approval-required* fact (the ticket's validation profile requires human
  approval before the merge executes). The wait is parked here; it is **never**
  resolved by this module — resuming is the user-decision driver below.
- ``WAITING_FOR_USER → {FAILED | READY_FOR_MERGE | <resumed stage>}``
  (SFP-141, the resolve edge of the SFP-140 park) — fired by a *user decision*
  fact (a confirmed ``UserDecision``-style observation, ID-069). ``REJECT``
  ends the workflow failed; ``APPROVE`` releases the parked merge
  (ID-024); ``ANSWER`` resumes the stage the decision names.
- ``<active or waiting> → FAILED`` (SFP-141, the terminal-failure edge) —
  fired by a *should-fail* fact (the landed SFP-144 ``ShouldFailPolicy``
  verdict). The driver **consumes** the boolean only; it never re-derives
  failure genuineness — the taxonomy is the policy's, consumed not duplicated
  (ID-068).

On a failed, absent, or non-matching fact there is **no** transition, and the
non-move is recorded as a business fact (§8.8) rather than swallowed —
including an ``APPROVED`` review fact, which does *not* loop back to coding:
the merge stage it selects is driven below, so within the coding/review drivers
it is recorded as a non-move there.

Grounded in:
- MAS §8.5 — every significant transition produces an immutable
  ``WorkflowDecision`` recording why, applied policy, facts, aggregate changes,
  commands emitted, previous/resulting state.
- MAS §8.6 — the workflow advances only on *events / user decisions*; commands
  never modify workflow state. Each driver's only trigger is its stage fact
  (a ``CodingJobUpdated``- / review-verdict-style observation).
- MAS §8.7 — outputs are deterministic; each driver is a pure function of its
  inputs (AP-011: no I/O, no clock, no randomness).
- MAS §8.8 — failures are business facts too: the decision *not* to move is
  constructed and returned, never swallowed and never silently escalated.
- MAS §8.2 / SFP-137 — no implicit transitions: an illegal move raises
  :class:`~orchestrator.domain.workflow.state_machine.IllegalTransitionError`
  straight from the table guard; the driver never catches or softens it.
- ID-068 — a ``CHANGES_REQUESTED`` review is the expected coder↔reviewer
  quality loop: it drives the rework edge
  ``REVIEW_IN_PROGRESS → CODING_IN_PROGRESS`` and never enters ``FAILED`` or
  any escalation path.
- ID-024 — a workflow whose validation profile requires human approval parks in
  ``WAITING_FOR_USER`` while the approval is outstanding; the parking edge is a
  first-class workflow move (``MERGING → WAITING_FOR_USER`` here), and leaving
  the wait happens only on the user's decision (§8.9 / ID-069) — never here.
- ID-067 — ``REQUIRES_HUMAN_APPROVAL`` is the profile set that makes the
  approval-required fact true; the driver consumes the already-evaluated fact
  and does not import the profile machinery.
- ID-072 — the merge *decision* is the Orchestrator's and its *execution* is
  the Workspace Worker's; the merge-stage driver only moves the workflow to
  ``MERGING`` on the approval fact.
- ID-069 — a user decision is only ever a confirmed, structured ``UserDecision``
  fact (the v0 vocabulary: ``REJECT``, ``APPROVE``, ``ANSWER``); the
  user-decision driver consumes that fact and never observes raw chat.
- §8.9 — users influence the workflow only through decisions; the
  user-decision driver is the single place a WAITING_FOR_USER wait resolves.
- ID-013 — enums serialize as plain strings in any serialized field (the
  ``WorkflowDecision`` ``*_name`` companions).
- SFP-142 — the policy engine that will own real policy selection is landed as
  a separate engine; these drivers carry the one trivial fixed rule each needs
  as module-level data, not as a policy abstraction.

Each driver implements the SFP-137 ``TransitionDriver`` seam shape: it observes
its stage's facts and requests the decided move through the engine. The module
adds no workflow states, duplicates no transition-table data, and performs no
bus I/O — the bus remains an injected seam handled by
:class:`~orchestrator.domain.workflow.state_machine.WorkflowTransitionPublisher`
outside this module.
"""

from __future__ import annotations

from orchestrator.domain.workflow.state_machine import TRANSITIONS, WorkflowDecision, transition
from orchestrator.domain.workflow.states import STATES, TERMINAL_STATES, WorkflowState

#: The single stage edge each driver owns (§8.4 order). Taken from the SFP-137
#: table's semantics; the legality check itself lives in ``TRANSITIONS`` and is
#: never re-implemented here (zero duplication).
SPEC_STAGE_SOURCE = WorkflowState.READY_FOR_PR_SPECIFICATION
SPEC_STAGE_TARGET = WorkflowState.READY_FOR_CODING
CODING_STAGE_SOURCE = WorkflowState.READY_FOR_CODING
CODING_STAGE_TARGET = WorkflowState.CODING_IN_PROGRESS
REVIEW_STAGE_SOURCE = WorkflowState.CODING_IN_PROGRESS
REVIEW_STAGE_TARGET = WorkflowState.REVIEW_IN_PROGRESS
#: The ID-068 rework edge: REVIEW_IN_PROGRESS loops back to CODING_IN_PROGRESS
#: on a CHANGES_REQUESTED review. Normal progression — never FAILED, never an
#: escalation.
REWORK_SOURCE = WorkflowState.REVIEW_IN_PROGRESS
REWORK_TARGET = WorkflowState.CODING_IN_PROGRESS
#: The SFP-140 merge-stage edge: the approved review verdict begins the merge
#: (the merge *decision* stays the Orchestrator's, ID-072).
MERGE_STAGE_SOURCE = WorkflowState.READY_FOR_MERGE
MERGE_STAGE_TARGET = WorkflowState.MERGING
#: The SFP-140 deploy-stage edges: MERGING begins deploying once the merge is
#: observed complete with a deploy target present, and DEPLOYING completes the
#: workflow when the deployment is observed succeeded.
DEPLOY_STAGE_BEGIN_SOURCE = WorkflowState.MERGING
DEPLOY_STAGE_BEGIN_TARGET = WorkflowState.DEPLOYING
DEPLOY_STAGE_FINISH_SOURCE = WorkflowState.DEPLOYING
DEPLOY_STAGE_FINISH_TARGET = WorkflowState.COMPLETED
#: The SFP-140 / ID-024 parking edge: MERGING parks in WAITING_FOR_USER while a
#: human approval required by the validation profile is outstanding. The wait
#: is *parked* here and never resolved here — resuming is the user-decision
#: edge's concern (ID-069 / §8.9), out of scope for this module.
MERGE_WAIT_SOURCE = WorkflowState.MERGING
MERGE_WAIT_TARGET = WorkflowState.WAITING_FOR_USER
#: The SFP-141 user-decision resolve edge source: the parked wait itself. The
#: targets it may resolve to are exactly the WAITING_FOR_USER row of the
#: SFP-137 table (derived below — never re-listed by hand, so the driver and
#: the table can never drift).
USER_DECISION_SOURCE = WorkflowState.WAITING_FOR_USER
#: The SFP-141 terminal-failure edge target. Legality itself lives in the
#: SFP-137 table (reachable from ``ACTIVE_STATES`` and from
#: ``WAITING_FOR_USER``); the driver never re-implements that set.
TERMINAL_FAILURE_TARGET = WorkflowState.FAILED

#: The trivial fixed policies these drivers apply (SFP-142 owns real policy
#: selection; a fixed rule is acceptable now per the PRSpec notes).
#: Recorded verbatim on every decision this module produces.
SPEC_STAGE_POLICY = "spec-stage-successful-plan"
CODING_STAGE_POLICY = "coding-stage-job-started"
REVIEW_STAGE_POLICY = "review-stage-pr-opened"
REWORK_POLICY = "review-stage-changes-requested-rework"
MERGE_STAGE_POLICY = "merge-stage-approval-granted"
DEPLOY_STAGE_POLICY = "deploy-stage-merge-completed-deploy-target"
MERGE_WAIT_POLICY = "merge-stage-approval-required-parking"
USER_DECISION_POLICY = "user-decision-observed"
TERMINAL_FAILURE_POLICY = "terminal-failure-policy-verdict"

#: The agents whose outputs these stages consume. The canonical agent
#: identifiers (workspace-worker agents) — kept as data so the fact-type match
#: stays a plain comparison, not a class-identity check.
PLANNER_AGENT = "planner"
CODER_AGENT = "coder"
REVIEWER_AGENT = "reviewer"

#: The coding-job status that means "the coding job has started" — the
#: ``CodingJobUpdated``-style observation's ``status`` vocabulary, pinned in the
#: landed SFP-219 serde contract (``{"job_id": "job-7", "status": "running"}``).
CODING_JOB_RUNNING_STATUS = "running"

#: The review verdict that drives the ID-068 rework loop, from the landed
#: :class:`~sfp_contracts.agents.reviewer.ReviewStatus` vocabulary. The other
#: verdicts (notably ``APPROVED``) do not match this stage's driver: APPROVED
#: selects the merge stage (SFP-140) and is recorded here as a non-move (§8.8).
CHANGES_REQUESTED_STATUS = "CHANGES_REQUESTED"

#: The deployment-outcome vocabulary this module recognizes: ``deployment_status``
#: is honored only as ``None`` (no observation), ``"succeeded"``, or ``"failed"``.
#: Any other string is treated as an unrecognized observation — a recorded
#: no-move naming it, never an exception and never a guessed outcome.
DEPLOYMENT_SUCCEEDED_STATUS = "succeeded"
DEPLOYMENT_FAILED_STATUS = "failed"

#: The user-decision vocabulary this driver recognizes, pinned to the v0
#: ``UserDecision`` members (ID-069). Only these three resolve a wait:
#: ``REJECT`` ends the workflow failed, ``APPROVE`` releases the parked merge
#: (ID-024), and ``ANSWER`` resumes the stage the decision names. The rest of
#: the v0 vocabulary (REQUEST_CHANGES, PROVIDE_CONTEXT, ANSWER_QUESTION,
#: CLARIFICATION) is not a wait-resolving decision for this edge — any other
#: value (including those) is a recorded no-move, never an exception.
USER_DECISION_REJECT = "REJECT"
USER_DECISION_APPROVE = "APPROVE"
USER_DECISION_ANSWER = "ANSWER"
_RECOGNIZED_USER_DECISIONS = frozenset(
    {USER_DECISION_REJECT, USER_DECISION_APPROVE, USER_DECISION_ANSWER},
)

#: Reasons recorded on the moves (§8.5 ``reason``).
_SPEC_MOVE_REASON = "successful plan observed: planner produced validated PR-specs"
_CODING_MOVE_REASON = "coding job started: the Coder is executing the PR-spec"
_REVIEW_MOVE_REASON = "PR created and review requested: the Reviewer takes over"
_REWORK_MOVE_REASON = "changes-requested review observed: rework is normal progression (ID-068)"

#: Reasons recorded on the non-moves (§8.8 — the non-move is a recorded
#: business fact, not a swallowed branch).
_NO_FACT_REASON = "no plan fact observed: the workflow stays at this stage"
_FAILED_FACT_REASON = "plan fact not successful: the workflow stays at this stage"
_NO_CODING_FACT_REASON = "no coding job started fact observed: the workflow stays at this stage"
_NOT_RUNNING_CODING_FACT_REASON = "coding job not started: the workflow stays at this stage"
_NO_PR_FACT_REASON = "no PR-created fact observed: the workflow stays at this stage"
_ABSENT_PR_FACT_REASON = "PR-created fact absent/incomplete: the workflow stays at this stage"
_NO_REVIEW_FACT_REASON = "no review fact observed: the workflow stays at this stage"
_NO_REWORK_REVIEW_REASON = (
    "review fact is not CHANGES_REQUESTED: no coding/review-stage move applies; "
    "the merge stage it may select is SFP-140's concern"
)

#: Reasons recorded on the SFP-140 merge/deploy moves (§8.5 ``reason``).
_MERGE_MOVE_REASON = "approval granted: begin merge"
_DEPLOY_BEGIN_MOVE_REASON = "merge completed and deploy target present: begin deploy"
_DEPLOY_FINISH_MOVE_REASON = "deployment succeeded"
_MERGE_WAIT_MOVE_REASON = "approval required before merging: park in WAITING_FOR_USER (ID-024)"

#: Reasons recorded on the SFP-140 merge/deploy non-moves (§8.8).
_NO_APPROVAL_FACT_REASON = "no approval fact observed: the workflow stays at this stage"
_NOT_APPROVED_REASON = "approval fact not approved: the workflow stays at this stage"
_DEPLOY_FAILED_OUTCOME_REASON = (
    "deployment failed: failure handling belongs to the ShouldFail policy, not this driver"
)
_NO_DEPLOY_OUTCOME_REASON = "no deploy outcome observed"
_NOT_IN_DEPLOY_STAGE_REASON = "not in a deploy stage"
_MERGE_NOT_COMPLETED_REASON = "merge not completed"
_NO_DEPLOY_TARGET_REASON = "no deploy target: nothing to deploy"
_MERGE_WAIT_NOT_REQUIRED_REASON = "approval not required"
_NO_APPROVAL_REQUIRED_FACT_REASON = "no approval-required fact"

#: Reasons recorded on the SFP-141 user-decision moves (§8.5 ``reason``).
_REJECT_MOVE_REASON = "user REJECT: workflow ends failed"
_APPROVE_MOVE_REASON = "user APPROVE: merge may proceed"
_ANSWER_MOVE_REASON_TEMPLATE = "user answer received: resume {target}"

#: Reasons recorded on the SFP-141 user-decision non-moves (§8.8).
_NO_USER_DECISION_REASON = "no user decision observed"
_UNRECOGNIZED_USER_DECISION_REASON = "user decision unrecognized"
_NOT_WAITING_FOR_USER_REASON = "not WAITING_FOR_USER: no wait to resolve"
_ANSWER_WITHOUT_TARGET_REASON = "answer without a recognized target stage"

#: Reasons recorded on the SFP-141 terminal-failure move / non-moves.
_TERMINAL_FAILURE_MOVE_REASON_TEMPLATE = (
    "genuine failure ({category}/{cause}): workflow ends failed"
)
_NO_FAILURE_FACT_REASON = "no failure fact observed"
_SHOULD_NOT_FAIL_REASON = (
    "failure policy says no failed move: rework/retry/wait semantics belong to the policy"
)
_ALREADY_TERMINAL_REASON = "already terminal"


def spec_stage_fact_is_successful(
    *,
    agent: str | None,
    status: str | None,
    pr_spec_ids: tuple[str, ...] = (),
) -> bool:
    """Return whether one observed fact is a *successful-plan* fact.

    The fact shape is the landed contract combination (MAS §12.9: taken from
    what is landed, not invented):

    - ``agent == "planner"`` — the producer side of the
      ``PRSpecificationsUpdated``-style observation;
    - ``status == "SUCCESS"`` — the
      :class:`~sfp_contracts.agents.status.AgentStatus` terminal status of the
      planner run that emitted it;
    - at least one ``pr_spec_ids`` entry — a successful
      :class:`~sfp_contracts.agents.planner.PlannerOutput` always carries
      ``pr_specs`` (``min_length=1``), so a success-shaped fact with zero
      PR-specs is not a plan-completed fact.

    Kept as a plain, total predicate over strings: pure, deterministic, and
    independent of any envelope class (AP-011).
    """
    if agent != PLANNER_AGENT:
        return False
    if status != "SUCCESS":
        return False
    return len(pr_spec_ids) > 0


def drive_spec_stage(
    current_state: WorkflowState,
    *,
    plan_fact: tuple[str | None, str | None, tuple[str, ...]] | None = None,
) -> tuple[WorkflowState, WorkflowDecision]:
    """Advance (or deliberately hold) the ticket→PR-spec stage (SFP-138).

    The pure stage driver: a function of ``(current_state, plan_fact)`` only —
    no I/O, no clock, no randomness, no bus (AP-011). Identical inputs always
    yield an identical resulting state and an equal ``WorkflowDecision``.

    Args:
        current_state: The workflow's current state.
        plan_fact: The observed plan-stage business fact as
            ``(agent, status, pr_spec_ids)`` — or ``None`` when no plan fact
            has been observed at all. ``None`` members inside the tuple mean
            the corresponding field was absent from the observation.

    Returns:
        The resulting workflow state and the immutable :class:`WorkflowDecision`
        recording what was decided and why. On a successful-plan fact the
        decision is the engine-produced §8.5 record of the move; on a
        failed/absent fact the decision is the §8.8 record of the *non-move*
        (same state on both endpoints, ``reason`` naming the cause). The
        non-move is returned, never swallowed.

    Raises:
        IllegalTransitionError: if the move is not in the SFP-137 transition
            table — e.g. the workflow is not currently at
            ``READY_FOR_PR_SPECIFICATION`` when a successful-plan fact arrives.
            Propagated from the table guard; the driver never performs or
            invents an implicit fallback transition (MAS §8.2).
    """
    if plan_fact is None or not spec_stage_fact_is_successful(
        agent=plan_fact[0],
        status=plan_fact[1],
        pr_spec_ids=plan_fact[2],
    ):
        # §8.8: the decision not to move is itself a recorded business fact —
        # same state on both endpoints, cause named in the reason. The workflow
        # is NOT failed here and NOT moved: planning simply has not completed.
        # This is a *record*, not a move, so it is constructed directly as a
        # WorkflowDecision (a same-state decision is a legitimate §8.5 record);
        # routing it through transition() would be wrong — no state has a
        # self-edge in TRANSITIONS, and none should.
        reason = _NO_FACT_REASON if plan_fact is None else _FAILED_FACT_REASON
        fact_id = "plan-fact:absent" if plan_fact is None else _fact_id(plan_fact)
        return current_state, WorkflowDecision(
            previous_state=current_state,
            resulting_state=current_state,
            reason=reason,
            applied_policy=SPEC_STAGE_POLICY,
            business_facts_considered=(fact_id,),
            previous_state_name=current_state.name,
            resulting_state_name=current_state.name,
        )

    # Successful plan → request the move through the SFP-137 engine. The engine
    # is the sole legality authority: if the edge is not in TRANSITIONS the
    # IllegalTransitionError propagates (no implicit move, ever).
    return transition(
        current_state,
        SPEC_STAGE_TARGET,
        reason=_SPEC_MOVE_REASON,
        applied_policy=SPEC_STAGE_POLICY,
        business_facts_considered=(_fact_id(plan_fact),),
        aggregate_changes=("tickets.workflow_status",),
    )


def _fact_id(plan_fact: tuple[str | None, str | None, tuple[str, ...]]) -> str:
    """Build the deterministic business-fact identifier for §8.5 records."""
    agent, status, pr_spec_ids = plan_fact
    agent_part = agent if agent is not None else "unknown-agent"
    status_part = status if status is not None else "unknown-status"
    specs_part = ",".join(pr_spec_ids) if pr_spec_ids else "no-pr-specs"
    return f"plan-fact:{agent_part}:{status_part}:{specs_part}"


# --- Coding stage: READY_FOR_CODING → CODING_IN_PROGRESS -----------------------


def coding_job_started_fact(
    *,
    job_status: str | None,
) -> bool:
    """Return whether one observed fact is a *coding job started* fact.

    The fact shape is the landed ``CodingJobUpdated``-style observation
    (MAS §12.9: taken from what is landed, not invented): the event carries
    ``job_id`` + ``status``, and "started" means ``status == "running"`` — the
    running-state value pinned by the landed SFP-219 serde contract. Kept as a
    plain, total predicate over strings: pure, deterministic, and independent
    of any envelope class (AP-011).
    """
    return job_status == CODING_JOB_RUNNING_STATUS


def _coding_fact_id(job_status: str | None) -> str:
    """Build the deterministic coding-fact identifier for §8.5 records."""
    status_part = job_status if job_status is not None else "unknown-status"
    return f"coding-job-fact:{status_part}"


def drive_coding_stage(
    current_state: WorkflowState,
    *,
    coding_fact: str | None = None,
) -> tuple[WorkflowState, WorkflowDecision]:
    """Advance (or deliberately hold) the coding-start stage (SFP-139).

    The pure stage driver: a function of ``(current_state, coding_fact)`` only —
    no I/O, no clock, no randomness, no bus (AP-011). Identical inputs always
    yield an identical resulting state and an equal ``WorkflowDecision``.

    Args:
        current_state: The workflow's current state.
        coding_fact: The observed coding-job business fact — the ``status``
            string of a ``CodingJobUpdated``-style observation (``"running"``
            means the job started) — or ``None`` when no coding fact has been
            observed at all.

    Returns:
        The resulting workflow state and the immutable :class:`WorkflowDecision`
        recording what was decided and why. On a started-job fact the decision
        is the engine-produced §8.5 record of the move
        ``READY_FOR_CODING → CODING_IN_PROGRESS``; on a not-yet-started or
        absent fact the decision is the §8.8 record of the *non-move* (same
        state on both endpoints, ``reason`` naming the cause). The non-move is
        returned, never swallowed.

    Raises:
        IllegalTransitionError: if the move is not in the SFP-137 transition
            table — e.g. the workflow is not currently at ``READY_FOR_CODING``
            when a job-started fact arrives. Propagated from the table guard;
            the driver never performs or invents an implicit fallback
            transition (MAS §8.2).
    """
    if coding_fact is None or not coding_job_started_fact(job_status=coding_fact):
        # §8.8: the decision not to move is itself a recorded business fact —
        # same state on both endpoints, cause named in the reason. A queued /
        # not-yet-started job is not an error (ID-068): the workflow simply has
        # not entered coding yet. Constructed directly as a WorkflowDecision (a
        # same-state decision is a legitimate §8.5 record); routing it through
        # transition() would be wrong — no state has a self-edge in TRANSITIONS.
        reason = (
            _NO_CODING_FACT_REASON if coding_fact is None else (_NOT_RUNNING_CODING_FACT_REASON)
        )
        fact_id = (
            "coding-job-fact:absent" if coding_fact is None else (_coding_fact_id(coding_fact))
        )
        return current_state, WorkflowDecision(
            previous_state=current_state,
            resulting_state=current_state,
            reason=reason,
            applied_policy=CODING_STAGE_POLICY,
            business_facts_considered=(fact_id,),
            previous_state_name=current_state.name,
            resulting_state_name=current_state.name,
        )

    # Job started → request the move through the SFP-137 engine. The engine is
    # the sole legality authority: if the edge is not in TRANSITIONS the
    # IllegalTransitionError propagates (no implicit move, ever).
    return transition(
        current_state,
        CODING_STAGE_TARGET,
        reason=_CODING_MOVE_REASON,
        applied_policy=CODING_STAGE_POLICY,
        business_facts_considered=(_coding_fact_id(coding_fact),),
        aggregate_changes=("tickets.workflow_status",),
    )


# --- Review stage: CODING_IN_PROGRESS → REVIEW_IN_PROGRESS --------------------


def _pr_fact_id(pr_fact: tuple[str | None, str | None]) -> str:
    """Build the deterministic PR-created-fact identifier for §8.5 records."""
    agent_part = pr_fact[0] if pr_fact[0] is not None else "unknown-agent"
    branch_part = pr_fact[1] if pr_fact[1] is not None else "no-branch"
    return f"pr-fact:{agent_part}:{branch_part}"


def pr_created_fact(
    *,
    agent: str | None,
    branch_name: str | None,
) -> bool:
    """Return whether one observed fact is a *PR created / review requested* fact.

    The fact shape is the landed ``CoderOutput`` contract (MAS §12.9: taken
    from what is landed, not invented): the Coder's implementation-evidence
    record carries ``branch_name`` (and the PR it references), so a fact with
    the Coder as producer and a non-empty ``branch_name`` is the "PR created /
    review requested" business fact. Kept as a plain, total predicate over
    strings: pure, deterministic, and independent of any envelope class
    (AP-011).
    """
    if agent != CODER_AGENT:
        return False
    return bool(branch_name)


def drive_review_stage(
    current_state: WorkflowState,
    *,
    pr_fact: tuple[str | None, str | None] | None = None,
) -> tuple[WorkflowState, WorkflowDecision]:
    """Advance (or deliberately hold) the coding→review stage (SFP-139).

    The pure stage driver: a function of ``(current_state, pr_fact)`` only —
    no I/O, no clock, no randomness, no bus (AP-011). Identical inputs always
    yield an identical resulting state and an equal ``WorkflowDecision``.

    Args:
        current_state: The workflow's current state.
        pr_fact: The observed *PR created / review requested* business fact as
            ``(agent, branch_name)`` — the producer agent and the branch the PR
            was opened from — or ``None`` when no such fact has been observed
            at all. ``None`` members inside the tuple mean the corresponding
            field was absent from the observation.

    Returns:
        The resulting workflow state and the immutable :class:`WorkflowDecision`
        recording what was decided and why. On a PR-created fact the decision
        is the engine-produced §8.5 record of the move
        ``CODING_IN_PROGRESS → REVIEW_IN_PROGRESS``; on an absent/incomplete or
        wrongly-produced fact the decision is the §8.8 record of the
        *non-move*. The non-move is returned, never swallowed.

    Raises:
        IllegalTransitionError: if the move is not in the SFP-137 transition
            table — e.g. the workflow is not currently at
            ``CODING_IN_PROGRESS`` when a PR-created fact arrives. Propagated
            from the table guard; the driver never performs or invents an
            implicit fallback transition (MAS §8.2).
    """
    if pr_fact is None:
        # §8.8: no observation yet → record the non-move, hold the state.
        return current_state, WorkflowDecision(
            previous_state=current_state,
            resulting_state=current_state,
            reason=_NO_PR_FACT_REASON,
            applied_policy=REVIEW_STAGE_POLICY,
            business_facts_considered=("pr-fact:absent",),
            previous_state_name=current_state.name,
            resulting_state_name=current_state.name,
        )

    if not pr_created_fact(agent=pr_fact[0], branch_name=pr_fact[1]):
        # §8.8: a fact was observed but it is not a PR-created fact (wrong
        # producer, or no branch yet — the Coder has not finished). Record the
        # non-move; the workflow stays in coding.
        return current_state, WorkflowDecision(
            previous_state=current_state,
            resulting_state=current_state,
            reason=_ABSENT_PR_FACT_REASON,
            applied_policy=REVIEW_STAGE_POLICY,
            business_facts_considered=(_pr_fact_id(pr_fact),),
            previous_state_name=current_state.name,
            resulting_state_name=current_state.name,
        )

    # PR created / review requested → request the move through the SFP-137
    # engine. The engine is the sole legality authority: if the edge is not in
    # TRANSITIONS the IllegalTransitionError propagates (no implicit move).
    return transition(
        current_state,
        REVIEW_STAGE_TARGET,
        reason=_REVIEW_MOVE_REASON,
        applied_policy=REVIEW_STAGE_POLICY,
        business_facts_considered=(_pr_fact_id(pr_fact),),
        aggregate_changes=("tickets.workflow_status",),
    )


# --- Rework loop: REVIEW_IN_PROGRESS → CODING_IN_PROGRESS (ID-068) -------------


def _review_fact_id(review_fact: tuple[int | None, str | None]) -> str:
    """Build the deterministic review-fact identifier for §8.5 records."""
    pr_part = str(review_fact[0]) if review_fact[0] is not None else "no-pr"
    status_part = review_fact[1] if review_fact[1] is not None else "unknown-status"
    return f"review-fact:{pr_part}:{status_part}"


def changes_requested_review_fact(
    *,
    review_status: str | None,
) -> bool:
    """Return whether one observed review fact is a *changes-requested* fact.

    The fact shape is the landed ``ReviewUpdated``-style observation
    (MAS §12.9: taken from what is landed, not invented): the event carries
    ``pr_number`` + ``review_status``, and ``review_status`` uses the
    :class:`~sfp_contracts.agents.reviewer.ReviewStatus` vocabulary. Only
    ``CHANGES_REQUESTED`` matches: per ID-068 that verdict is the expected
    coder↔reviewer quality loop (normal progression, never a failure), while
    ``APPROVED`` selects the merge stage (SFP-140) and the remaining verdicts
    are not this driver's concern. Kept as a plain, total predicate over
    strings: pure, deterministic, and independent of any envelope class
    (AP-011).
    """
    return review_status == CHANGES_REQUESTED_STATUS


def drive_rework_loop(
    current_state: WorkflowState,
    *,
    review_fact: tuple[int | None, str | None] | None = None,
) -> tuple[WorkflowState, WorkflowDecision]:
    """Drive (or deliberately hold) the ID-068 rework loop (SFP-139).

    The pure rework driver: a function of ``(current_state, review_fact)``
    only — no I/O, no clock, no randomness, no bus (AP-011). Identical inputs
    always yield an identical resulting state and an equal ``WorkflowDecision``.

    ID-068: a ``CHANGES_REQUESTED`` review is the expected coder↔reviewer
    quality loop — *normal workflow progression*, never a failure. This driver
    performs ``REVIEW_IN_PROGRESS → CODING_IN_PROGRESS`` and produces **no**
    ``FAILED`` state and **no** escalation of any kind; there is no code path
    here that could (the only target it ever requests is
    :data:`REWORK_TARGET`).

    Args:
        current_state: The workflow's current state.
        review_fact: The observed review-verdict business fact as
            ``(pr_number, review_status)`` — the landed ``ReviewUpdated``-style
            observation shape — or ``None`` when no review fact has been
            observed at all. ``None`` members inside the tuple mean the
            corresponding field was absent from the observation.

    Returns:
        The resulting workflow state and the immutable :class:`WorkflowDecision`
        recording what was decided and why. On a CHANGES_REQUESTED review the
        decision is the engine-produced §8.5 record of the rework move back to
        ``CODING_IN_PROGRESS`` — never ``FAILED``, never an escalation (ID-068).
        On any other review verdict — notably ``APPROVED``, whose merge-stage
        move is SFP-140's concern — the decision is the §8.8 record of the
        *non-move* (same state on both endpoints, ``reason`` naming the cause).
        The non-move is returned, never swallowed.

    Raises:
        IllegalTransitionError: if the move is not in the SFP-137 transition
            table — e.g. the workflow is not currently at ``REVIEW_IN_PROGRESS``
            when a CHANGES_REQUESTED review arrives. Propagated from the table
            guard; the driver never performs or invents an implicit fallback
            transition (MAS §8.2).
    """
    if review_fact is None or not changes_requested_review_fact(
        review_status=review_fact[1],
    ):
        # §8.8: the decision not to move is itself a recorded business fact —
        # same state on both endpoints, cause named in the reason. An APPROVED
        # review is NOT a coding/review-stage move: the merge stage it selects
        # is SFP-140's concern, so it is recorded here as a non-move and never
        # swallowed. No FAILED, no escalation (ID-068).
        reason = _NO_REVIEW_FACT_REASON if review_fact is None else (_NO_REWORK_REVIEW_REASON)
        fact_id = "review-fact:absent" if review_fact is None else (_review_fact_id(review_fact))
        return current_state, WorkflowDecision(
            previous_state=current_state,
            resulting_state=current_state,
            reason=reason,
            applied_policy=REWORK_POLICY,
            business_facts_considered=(fact_id,),
            previous_state_name=current_state.name,
            resulting_state_name=current_state.name,
        )

    # CHANGES_REQUESTED → request the rework move through the SFP-137 engine
    # (ID-068: normal progression). The engine is the sole legality authority:
    # if the edge is not in TRANSITIONS the IllegalTransitionError propagates
    # (no implicit move, ever). The target is always CODING_IN_PROGRESS — this
    # driver can never request FAILED or any escalation.
    return transition(
        current_state,
        REWORK_TARGET,
        reason=_REWORK_MOVE_REASON,
        applied_policy=REWORK_POLICY,
        business_facts_considered=(_review_fact_id(review_fact),),
        aggregate_changes=("tickets.workflow_status",),
    )


# --- Merge stage: READY_FOR_MERGE → MERGING (SFP-140) ---------------------------


def _approval_fact_id(approval_fact: tuple[bool | None, str | None]) -> str:
    """Build the deterministic approval-fact identifier for §8.5 records."""
    approved = approval_fact[0]
    approved_part = str(approved).lower() if approved is not None else "unknown-approval"
    profile_part = approval_fact[1] if approval_fact[1] is not None else "unknown-profile"
    return f"approval-fact:{approved_part}:{profile_part}"


def approval_fact_granted(*, approved: bool | None) -> bool:
    """Return whether one observed approval fact is an *approval granted* fact.

    The fact shape is the landed approval observation (MAS §12.9: taken from
    what is landed, not invented): ``(approved, validation_profile)`` — whether
    the review verdict approved the PR, and the validation profile under which
    the ticket is being run. Only ``approved is True`` begins the merge: an
    absent (``None``) or explicitly not-approved (``False``) verdict — including
    a ``CHANGES_REQUESTED`` one — never merges (§8.8). Kept as a plain, total
    predicate: pure, deterministic, and independent of any envelope class
    (AP-011).
    """
    return approved is True


def drive_merge_stage(
    current_state: WorkflowState,
    *,
    approval_fact: tuple[bool | None, str | None] | None = None,
) -> tuple[WorkflowState, WorkflowDecision]:
    """Advance (or deliberately hold) the approval→merge stage (SFP-140).

    The pure merge-stage driver: a function of ``(current_state,
    approval_fact)`` only — no I/O, no clock, no randomness, no bus (AP-011).
    Identical inputs always yield an identical resulting state and an equal
    ``WorkflowDecision``.

    Args:
        current_state: The workflow's current state.
        approval_fact: The observed approval business fact as
            ``(approved, validation_profile)`` — whether the review approved
            the PR and the profile the ticket is running under — or ``None``
            when no approval fact has been observed at all. A ``None``
            ``approved`` member means the verdict was absent from the
            observation.

    Returns:
        The resulting workflow state and the immutable :class:`WorkflowDecision`
        recording what was decided and why. On an approved fact the decision is
        the engine-produced §8.5 record of the move
        ``READY_FOR_MERGE → MERGING``; on an absent or not-approved fact the
        decision is the §8.8 record of the *non-move* (same state on both
        endpoints, ``reason`` naming the observed verdict). The non-move is
        returned, never swallowed.

    Raises:
        IllegalTransitionError: if the move is not in the SFP-137 transition
            table — e.g. the workflow is not currently at ``READY_FOR_MERGE``
            when an approval fact arrives. Propagated from the table guard; the
            driver never performs or invents an implicit fallback transition
            (MAS §8.2).
    """
    if approval_fact is None:
        # §8.8: no observation yet → record the non-move, hold the state.
        return current_state, WorkflowDecision(
            previous_state=current_state,
            resulting_state=current_state,
            reason=_NO_APPROVAL_FACT_REASON,
            applied_policy=MERGE_STAGE_POLICY,
            business_facts_considered=("approval-fact:absent",),
            previous_state_name=current_state.name,
            resulting_state_name=current_state.name,
        )

    if not approval_fact_granted(approved=approval_fact[0]):
        # §8.8: a fact was observed but it is not an approval — an absent
        # verdict or a CHANGES_REQUESTED one never merges. Record the non-move
        # naming the observed verdict; the workflow stays ready for merge.
        return current_state, WorkflowDecision(
            previous_state=current_state,
            resulting_state=current_state,
            reason=_NOT_APPROVED_REASON,
            applied_policy=MERGE_STAGE_POLICY,
            business_facts_considered=(_approval_fact_id(approval_fact),),
            previous_state_name=current_state.name,
            resulting_state_name=current_state.name,
        )

    # Approval granted → request the move through the SFP-137 engine. The
    # engine is the sole legality authority: if the edge is not in TRANSITIONS
    # the IllegalTransitionError propagates (no implicit move, ever).
    return transition(
        current_state,
        MERGE_STAGE_TARGET,
        reason=_MERGE_MOVE_REASON,
        applied_policy=MERGE_STAGE_POLICY,
        business_facts_considered=(_approval_fact_id(approval_fact),),
        aggregate_changes=("tickets.workflow_status",),
    )


# --- Deploy stage: MERGING → DEPLOYING → COMPLETED (SFP-140) --------------------


def _deploy_fact_id(
    *,
    merge_completed: bool | None,
    deploy_target_ref: str | None,
) -> str:
    """Build the deterministic deploy-target-fact identifier for §8.5 records."""
    completed_part = (
        str(merge_completed).lower() if merge_completed is not None else "unknown-merge-completed"
    )
    # An empty ref is the same observation as an absent one (nothing to deploy),
    # so it renders identically — never as a dangling empty segment.
    target_part = deploy_target_ref if deploy_target_ref else "no-deploy-target"
    return f"deploy-fact:{completed_part}:{target_part}"


def _deploy_outcome_fact_id(deployment_status: str) -> str:
    """Build the deterministic deploy-outcome-fact identifier for §8.5 records."""
    return f"deploy-outcome-fact:{deployment_status}"


def deploy_outcome_recognized(*, deployment_status: str | None) -> bool:
    """Return whether one observed deployment status is a recognized outcome.

    The fact shape is the landed deployment-outcome observation (MAS §12.9:
    taken from what is landed, not invented): ``deployment_status`` is honored
    only as ``"succeeded"`` or ``"failed"``. ``None`` means no outcome has been
    observed, and any other string is an unrecognized vocabulary value — in
    both cases there is no recognized outcome yet, which the driver records as
    a no-move naming what it saw (never an exception, never a guess). Kept as a
    plain, total predicate: pure, deterministic (AP-011).
    """
    return deployment_status in _RECOGNIZED_DEPLOY_OUTCOMES


#: The only deployment-outcome values this module recognizes (a frozenset so
#: the predicate stays a plain membership test, not a chain of comparisons).
_RECOGNIZED_DEPLOY_OUTCOMES = frozenset(
    {DEPLOYMENT_SUCCEEDED_STATUS, DEPLOYMENT_FAILED_STATUS},
)


def drive_deploy_stage(
    current_state: WorkflowState,
    *,
    merge_completed: bool | None = None,
    deploy_target_ref: str | None = None,
    deployment_status: str | None = None,
) -> tuple[WorkflowState, WorkflowDecision]:
    """Advance (or deliberately hold) the merge→deploy→completed stage (SFP-140).

    The pure deploy-stage driver: a function of ``(current_state,
    merge_completed, deploy_target_ref, deployment_status)`` only — no I/O, no
    clock, no randomness, no bus (AP-011). Identical inputs always yield an
    identical resulting state and an equal ``WorkflowDecision``.

    The decision table, verbatim:

    ======  ==========================  ==========================  =====================
    Row     State                       Facts                       Outcome
    ======  ==========================  ==========================  =====================
    1       ``DEPLOYING``               status ``"succeeded"``      → ``COMPLETED``
    2       ``DEPLOYING``               status ``"failed"``         no-move (§8.8) — the
                                                                      landed SFP-144
                                                                      ``ShouldFailPolicy``
                                                                      owns failure
    3       ``DEPLOYING``               status ``None``/other       no-move — no recognized
                                                                      outcome observed
    4       ``MERGING``                 ``merge_completed is True`` → ``DEPLOYING``
                                        **and** ``deploy_target_ref``
    5       ``MERGING``                 row 4's facts not met        no-move, naming which
    6       any other state             —                           no-move — not a deploy
                                                                      stage
    ======  ==========================  ==========================  =====================

    At ``MERGING`` the deployment outcome is deliberately **not** consulted:
    no deployment exists yet, so a stray outcome observation cannot skip the
    deploy stage.

    Args:
        current_state: The workflow's current state.
        merge_completed: Whether the merge was observed completed (the SFP-240
            gate context is the source of this observation) — ``None`` when
            not observed.
        deploy_target_ref: The ref the merged work is deployed to — ``None`` /
            empty when there is nothing to deploy yet.
        deployment_status: The deployment outcome, honored only as
            ``"succeeded"`` / ``"failed"``. Any other value (including an
            arbitrary unrecognized string) is treated as an unrecognized
            observation and recorded as a no-move naming it — never an
            exception.

    Returns:
        The resulting workflow state and the immutable :class:`WorkflowDecision`
        recording what was decided and why. A move is requested only on rows 1
        and 4, through the SFP-137 engine; every other row returns the §8.8
        record of the *non-move* (same state on both endpoints, ``reason``
        naming the exact cause). The non-move is returned, never swallowed —
        and this driver **never** requests ``FAILED`` (row 2 defers to the
        landed ``ShouldFailPolicy``).

    Raises:
        IllegalTransitionError: if the decided move is not in the SFP-137
            transition table. Propagated from the table guard; the driver
            never performs or invents an implicit fallback transition
            (MAS §8.2).
    """
    if current_state is DEPLOY_STAGE_FINISH_SOURCE:
        return _drive_deploy_finish(current_state, deployment_status=deployment_status)

    if current_state is DEPLOY_STAGE_BEGIN_SOURCE:
        return _drive_deploy_begin(
            current_state,
            merge_completed=merge_completed,
            deploy_target_ref=deploy_target_ref,
        )

    # Row 6: any other state is not a deploy stage — record the non-move (§8.8)
    # rather than silently doing nothing.
    return current_state, WorkflowDecision(
        previous_state=current_state,
        resulting_state=current_state,
        reason=_NOT_IN_DEPLOY_STAGE_REASON,
        applied_policy=DEPLOY_STAGE_POLICY,
        business_facts_considered=(),
        previous_state_name=current_state.name,
        resulting_state_name=current_state.name,
    )


def _drive_deploy_finish(
    current_state: WorkflowState,
    *,
    deployment_status: str | None,
) -> tuple[WorkflowState, WorkflowDecision]:
    """Rows 1–3: decide the ``DEPLOYING`` outcome (SFP-140).

    Raises:
        IllegalTransitionError: if the row-1 move is not in the SFP-137 table.
    """
    if deployment_status == DEPLOYMENT_SUCCEEDED_STATUS:
        # Row 1: the deployment succeeded → the workflow completes.
        return transition(
            current_state,
            DEPLOY_STAGE_FINISH_TARGET,
            reason=_DEPLOY_FINISH_MOVE_REASON,
            applied_policy=DEPLOY_STAGE_POLICY,
            business_facts_considered=(_deploy_outcome_fact_id(deployment_status),),
            aggregate_changes=("tickets.workflow_status",),
        )

    if deployment_status == DEPLOYMENT_FAILED_STATUS:
        # Row 2: a failed deployment is NOT this driver's move — failure
        # handling belongs to the landed SFP-144 ShouldFailPolicy (§8.8: the
        # non-move is a recorded business fact; FAILED is never entered here).
        return current_state, WorkflowDecision(
            previous_state=current_state,
            resulting_state=current_state,
            reason=_DEPLOY_FAILED_OUTCOME_REASON,
            applied_policy=DEPLOY_STAGE_POLICY,
            business_facts_considered=(_deploy_outcome_fact_id(deployment_status),),
            previous_state_name=current_state.name,
            resulting_state_name=current_state.name,
        )

    # Row 3: no recognized outcome — unobserved (None) or an unrecognized
    # vocabulary value. Treat as absent, name what was seen (§8.8), never raise.
    return current_state, WorkflowDecision(
        previous_state=current_state,
        resulting_state=current_state,
        reason=_NO_DEPLOY_OUTCOME_REASON,
        applied_policy=DEPLOY_STAGE_POLICY,
        business_facts_considered=(
            _deploy_outcome_fact_id(deployment_status if deployment_status else "none"),
        ),
        previous_state_name=current_state.name,
        resulting_state_name=current_state.name,
    )


def _drive_deploy_begin(
    current_state: WorkflowState,
    *,
    merge_completed: bool | None,
    deploy_target_ref: str | None,
) -> tuple[WorkflowState, WorkflowDecision]:
    """Rows 4–5: decide the ``MERGING`` → deploy begin (SFP-140).

    Raises:
        IllegalTransitionError: if the row-4 move is not in the SFP-137 table.
    """
    fact_id = _deploy_fact_id(
        merge_completed=merge_completed,
        deploy_target_ref=deploy_target_ref,
    )

    # Row 5a: the merge has not been observed completed — nothing to deploy yet.
    if merge_completed is not True:
        return current_state, WorkflowDecision(
            previous_state=current_state,
            resulting_state=current_state,
            reason=_MERGE_NOT_COMPLETED_REASON,
            applied_policy=DEPLOY_STAGE_POLICY,
            business_facts_considered=(fact_id,),
            previous_state_name=current_state.name,
            resulting_state_name=current_state.name,
        )

    # Row 5b: the merge completed but no deploy target was observed — there is
    # nothing to deploy yet.
    if not deploy_target_ref:
        return current_state, WorkflowDecision(
            previous_state=current_state,
            resulting_state=current_state,
            reason=_NO_DEPLOY_TARGET_REASON,
            applied_policy=DEPLOY_STAGE_POLICY,
            business_facts_considered=(fact_id,),
            previous_state_name=current_state.name,
            resulting_state_name=current_state.name,
        )

    # Row 4: merge observed complete and a deploy target is present → begin
    # deploying. The engine is the sole legality authority; an illegal move
    # propagates (no implicit move, ever).
    return transition(
        current_state,
        DEPLOY_STAGE_BEGIN_TARGET,
        reason=_DEPLOY_BEGIN_MOVE_REASON,
        applied_policy=DEPLOY_STAGE_POLICY,
        business_facts_considered=(fact_id,),
        aggregate_changes=("tickets.workflow_status",),
    )


# --- Merge-wait parking: MERGING → WAITING_FOR_USER (ID-024) --------------------


def _approval_required_fact_id(approval_required_fact: bool | None) -> str:
    """Build the deterministic approval-required-fact identifier (§8.5)."""
    return (
        str(approval_required_fact).lower()
        if approval_required_fact is not None
        else "unknown-approval-required"
    )


def drive_merge_wait(
    current_state: WorkflowState,
    *,
    approval_required_fact: bool | None = None,
) -> tuple[WorkflowState, WorkflowDecision]:
    """Park (or deliberately hold) the MERGING approval wait (SFP-140, ID-024).

    The pure merge-wait driver: a function of ``(current_state,
    approval_required_fact)`` only — no I/O, no clock, no randomness, no bus
    (AP-011). Identical inputs always yield an identical resulting state and an
    equal ``WorkflowDecision``.

    ID-024: a workflow whose validation profile requires human approval
    (:data:`~sfp_contracts.validation.profiles.REQUIRES_HUMAN_APPROVAL`, ID-067)
    parks in ``WAITING_FOR_USER`` while the approval is outstanding. This
    driver owns only the **parking** edge ``MERGING → WAITING_FOR_USER``; it
    never resolves the wait — leaving ``WAITING_FOR_USER`` happens on the
    user's decision (§8.9 / ID-069), which is a user-decision edge driven by a
    user-decision observation, not by any stage fact, and is out of scope here.

    Args:
        current_state: The workflow's current state.
        approval_required_fact: The observed *approval required* business fact —
            ``True`` when the ticket's validation profile requires human
            approval, ``False`` when it does not — or ``None`` when no such
            fact has been observed at all.

    Returns:
        The resulting workflow state and the immutable :class:`WorkflowDecision`
        recording what was decided and why. On a ``True`` fact the decision is
        the engine-produced §8.5 record of the parking move
        ``MERGING → WAITING_FOR_USER``; on a ``False`` or absent fact the
        decision is the §8.8 record of the *non-move* (same state on both
        endpoints, ``reason`` naming the cause). The non-move is returned,
        never swallowed.

    Raises:
        IllegalTransitionError: if the move is not in the SFP-137 transition
            table — e.g. the workflow is not currently at ``MERGING`` when an
            approval-required fact arrives. Propagated from the table guard;
            the driver never performs or invents an implicit fallback
            transition (MAS §8.2).
    """
    if approval_required_fact is None:
        # §8.8: no observation yet → record the non-move, hold the state.
        return current_state, WorkflowDecision(
            previous_state=current_state,
            resulting_state=current_state,
            reason=_NO_APPROVAL_REQUIRED_FACT_REASON,
            applied_policy=MERGE_WAIT_POLICY,
            business_facts_considered=("approval-required-fact:absent",),
            previous_state_name=current_state.name,
            resulting_state_name=current_state.name,
        )

    if approval_required_fact is not True:
        # §8.8: approval is not required → no parking move; record the non-move
        # naming the observed fact and keep merging.
        return current_state, WorkflowDecision(
            previous_state=current_state,
            resulting_state=current_state,
            reason=_MERGE_WAIT_NOT_REQUIRED_REASON,
            applied_policy=MERGE_WAIT_POLICY,
            business_facts_considered=(
                f"approval-required-fact:{_approval_required_fact_id(approval_required_fact)}",
            ),
            previous_state_name=current_state.name,
            resulting_state_name=current_state.name,
        )

    # Approval required → park through the SFP-137 engine (ID-024). The engine
    # is the sole legality authority: if the edge is not in TRANSITIONS the
    # IllegalTransitionError propagates (no implicit move, ever). The wait is
    # parked, never resolved here.
    return transition(
        current_state,
        MERGE_WAIT_TARGET,
        reason=_MERGE_WAIT_MOVE_REASON,
        applied_policy=MERGE_WAIT_POLICY,
        business_facts_considered=(
            f"approval-required-fact:{_approval_required_fact_id(approval_required_fact)}",
        ),
        aggregate_changes=("tickets.workflow_status",),
    )


# --- User-decision continuation: WAITING_FOR_USER → {FAILED | READY_FOR_MERGE |
# --- <resumed stage>} (SFP-141, the resolve edge of the SFP-140 park) ------------


def _user_decision_fact_id(decision_fact: tuple[str | None, str | None]) -> str:
    """Build the deterministic user-decision-fact identifier for §8.5 records."""
    decision_part = decision_fact[0] if decision_fact[0] is not None else "no-decision"
    # An empty target is the same observation as an absent one (the decision
    # named no stage), so it renders identically — never a dangling segment.
    target_part = decision_fact[1] if decision_fact[1] else "no-target"
    return f"user-decision-fact:{decision_part}:{target_part}"


def user_decision_recognized(*, decision: str | None) -> bool:
    """Return whether one observed decision is a *wait-resolving* user decision.

    The fact shape is the landed confirmed ``UserDecision`` observation (MAS
    §12.9 / ID-069: taken from what is landed, not invented): only a
    structured, CONFIRM-gated decision reaches this driver — raw chat never
    does. Of the v0 vocabulary, exactly ``REJECT`` / ``APPROVE`` / ``ANSWER``
    resolve a wait; the remaining members (``REQUEST_CHANGES``,
    ``PROVIDE_CONTEXT``, ``ANSWER_QUESTION``, ``CLARIFICATION``) and any
    unrecognized or absent value do not, and are recorded as a no-move naming
    what was seen (never an exception, never a guess). Kept as a plain, total
    predicate: pure, deterministic (AP-011).
    """
    return decision in _RECOGNIZED_USER_DECISIONS


#: The stages a parked wait may resume to — read from the landed SFP-137
#: table's ``WAITING_FOR_USER`` row, never re-listed by hand. Exposed for
#: tests and diagnostics to assert against the table directly; the driver
#: itself does not consult it for control flow — every named stage is handed
#: to the engine, whose table guard is the sole legality authority (§8.2).
_WAIT_RESUME_TARGETS: frozenset[WorkflowState] = TRANSITIONS[WorkflowState.WAITING_FOR_USER]


def drive_user_decision(
    current_state: WorkflowState,
    *,
    decision_fact: tuple[str | None, str | None] | None = None,
) -> tuple[WorkflowState, WorkflowDecision]:
    """Resolve (or deliberately hold) a parked user wait (SFP-141).

    The pure user-decision driver: a function of ``(current_state,
    decision_fact)`` only — no I/O, no clock, no randomness, no bus (AP-011).
    Identical inputs always yield an identical resulting state and an equal
    ``WorkflowDecision``.

    This is the resolve counterpart of :func:`drive_merge_wait`'s park edge: a
    workflow parked in ``WAITING_FOR_USER`` leaves it **only** on the user's
    confirmed decision (§8.9 / ID-069). The decision table, verbatim:

    ======  =====================  =============================  ==========================
    Row     State                  Facts                         Outcome
    ======  =====================  =============================  ==========================
    1       any                    fact ``None``                 no-move (§8.8)
    2       any                    decision ``None``/other       no-move — unrecognized
    3       not ``WAITING_FOR_     any recognized decision       no-move — no wait to
            USER``                                               resolve
    4       ``WAITING_FOR_USER``   decision ``REJECT``           → ``FAILED`` (ID-069)
    5       ``WAITING_FOR_USER``   decision ``APPROVE``          → ``READY_FOR_MERGE``
                                                               (ID-024 merge release)
    6       ``WAITING_FOR_USER``   decision ``ANSWER`` + a       → resume ``<target>``
                                  recognized ``target``
    6b      ``WAITING_FOR_USER``   ``ANSWER`` naming no stage     no-move — no recognized
                                  (``None``/blank/not a stage)   target stage
    ======  =====================  =============================  ==========================

    Row 6's legal resume targets are the SFP-137 table's
    ``WAITING_FOR_USER`` row (:data:`_WAIT_RESUME_TARGETS`, derived — the
    driver never re-lists them); legality stays the engine's, not this
    driver's. An ANSWER that names no stage of the workflow
    at all is row 6b's recorded no-move. An ANSWER that names a stage flows
    through the engine whatever the stage is: a target in the resume row
    moves, and any other named target lets the table guard's
    :class:`IllegalTransitionError` propagate (§8.2) — never caught, never
    softened into a no-move.

    Args:
        current_state: The workflow's current state.
        decision_fact: The observed user-decision business fact as
            ``(decision, target)`` — the confirmed ``UserDecision`` value and,
            for an ``ANSWER``, the stage the answer resumes — or ``None`` when
            no user decision has been observed at all. ``None`` members inside
            the tuple mean the corresponding field was absent from the
            observation.

    Returns:
        The resulting workflow state and the immutable :class:`WorkflowDecision`
        recording what was decided and why. Rows 4–6 request their move through
        the SFP-137 engine (the engine is the sole legality authority); every
        other row returns the §8.8 record of the *non-move* (same state on
        both endpoints, ``reason`` naming the exact cause). The non-move is
        returned, never swallowed.

    Raises:
        IllegalTransitionError: if the decided move is not in the SFP-137
            transition table — e.g. an ``ANSWER`` naming a stage that is not
            in the ``WAITING_FOR_USER`` row of :data:`TRANSITIONS`. Propagated
            from the table guard; the driver never catches or softens it and
            never performs an implicit fallback transition (MAS §8.2).
    """
    if decision_fact is None:
        # Row 1 (§8.8): no observation yet → record the non-move, hold the state.
        return current_state, WorkflowDecision(
            previous_state=current_state,
            resulting_state=current_state,
            reason=_NO_USER_DECISION_REASON,
            applied_policy=USER_DECISION_POLICY,
            business_facts_considered=("user-decision-fact:absent",),
            previous_state_name=current_state.name,
            resulting_state_name=current_state.name,
        )

    if not user_decision_recognized(decision=decision_fact[0]):
        # Row 2 (§8.8): a decision was observed but it is not one of the three
        # wait-resolving values — absent, unrecognized, or a v0 member that
        # does not resolve this edge. Name what was seen; never raise.
        return current_state, WorkflowDecision(
            previous_state=current_state,
            resulting_state=current_state,
            reason=_UNRECOGNIZED_USER_DECISION_REASON,
            applied_policy=USER_DECISION_POLICY,
            business_facts_considered=(_user_decision_fact_id(decision_fact),),
            previous_state_name=current_state.name,
            resulting_state_name=current_state.name,
        )

    if current_state is not USER_DECISION_SOURCE:
        # Row 3 (§8.8): there is no parked wait to resolve — a user decision
        # lands only while WAITING_FOR_USER. Recorded, never an error.
        return current_state, WorkflowDecision(
            previous_state=current_state,
            resulting_state=current_state,
            reason=_NOT_WAITING_FOR_USER_REASON,
            applied_policy=USER_DECISION_POLICY,
            business_facts_considered=(_user_decision_fact_id(decision_fact),),
            previous_state_name=current_state.name,
            resulting_state_name=current_state.name,
        )

    decision, target_name = decision_fact

    if decision == USER_DECISION_REJECT:
        # Row 4 (ID-069): the user rejected — the workflow ends failed.
        return transition(
            current_state,
            TERMINAL_FAILURE_TARGET,
            reason=_REJECT_MOVE_REASON,
            applied_policy=USER_DECISION_POLICY,
            business_facts_considered=(_user_decision_fact_id(decision_fact),),
            aggregate_changes=("tickets.workflow_status",),
        )

    if decision == USER_DECISION_APPROVE:
        # Row 5 (ID-024): the user approved — the parked merge may proceed.
        return transition(
            current_state,
            MERGE_STAGE_SOURCE,
            reason=_APPROVE_MOVE_REASON,
            applied_policy=USER_DECISION_POLICY,
            business_facts_considered=(_user_decision_fact_id(decision_fact),),
            aggregate_changes=("tickets.workflow_status",),
        )

    # Row 6 / 6b (ANSWER): resume the stage the answer names. The recognized
    # target set is read from the table's WAITING_FOR_USER row. An ANSWER that
    # names no stage at all (absent / blank / not a workflow stage) is the
    # recorded no-move of row 6b — the observation is incomplete, so there is
    # nothing to resume and nothing to ask the engine about. An ANSWER that
    # *does* name a stage is handed to the engine as given: if that stage is
    # not in the table's resume row the IllegalTransitionError propagates
    # (§8.2) — the driver never filters a named stage into a soft no-move and
    # never substitutes a fallback target.
    resume_target = _answer_resume_target(target_name)
    if resume_target is None:
        return current_state, WorkflowDecision(
            previous_state=current_state,
            resulting_state=current_state,
            reason=_ANSWER_WITHOUT_TARGET_REASON,
            applied_policy=USER_DECISION_POLICY,
            business_facts_considered=(_user_decision_fact_id(decision_fact),),
            previous_state_name=current_state.name,
            resulting_state_name=current_state.name,
        )

    return transition(
        current_state,
        resume_target,
        reason=_ANSWER_MOVE_REASON_TEMPLATE.format(target=resume_target.name),
        applied_policy=USER_DECISION_POLICY,
        business_facts_considered=(_user_decision_fact_id(decision_fact),),
        aggregate_changes=("tickets.workflow_status",),
    )


def _answer_resume_target(target_name: str | None) -> WorkflowState | None:
    """Lift an ANSWER's resume target to a stage — never a legality verdict.

    Returns the landed :class:`WorkflowState` named by ``target_name``, or
    ``None`` when the answer names no stage of this workflow at all (absent,
    blank, or a string that is not one of the ten §8.4 stage names). Legality
    is deliberately **not** decided here: a target that names a real stage
    flows to the SFP-137 engine whatever the stage is, and if that stage is
    not in the table's ``WAITING_FOR_USER`` resume row the engine's guard
    raises :class:`IllegalTransitionError` (§8.2) — never caught, never
    softened into a no-move, never substituted with a fallback target.
    Read-only against the landed state set; the resume row itself is derived
    from :data:`TRANSITIONS`, never copied, so the driver cannot drift.

    The lookup compares names against :data:`STATES` rather than subscripting
    the enum: the stage names are data (the §8.4 pinned list), and a plain
    name comparison keeps the predicate total — no exception path for a string
    that names no stage — and stays independent of the enum's value scheme.
    """
    if not target_name:
        return None
    for state in STATES:
        if state.name == target_name:
            return state
    return None


# --- Terminal failure: <active or waiting> → FAILED (SFP-141) -------------------


def _failure_fact_id(
    failure_fact: tuple[bool | None, str | None, str | None],
) -> str:
    """Build the deterministic failure-fact identifier for §8.5 records."""
    should_fail = failure_fact[0]
    should_fail_part = (
        str(should_fail).lower() if should_fail is not None else "unknown-should-fail"
    )
    category_part = failure_fact[1] if failure_fact[1] else "no-category"
    cause_part = failure_fact[2] if failure_fact[2] else "no-cause"
    return f"failure-fact:{should_fail_part}:{category_part}:{cause_part}"


def drive_terminal_failure(
    current_state: WorkflowState,
    *,
    failure_fact: tuple[bool | None, str | None, str | None] | None = None,
) -> tuple[WorkflowState, WorkflowDecision]:
    """Consume the should-fail verdict into the ``FAILED`` edge (SFP-141).

    The pure terminal-failure driver: a function of ``(current_state,
    failure_fact)`` only — no I/O, no clock, no randomness, no bus (AP-011).
    Identical inputs always yield an identical resulting state and an equal
    ``WorkflowDecision``.

    The fact is the landed SFP-144 ``ShouldFailPolicy`` output shape
    (``(should_fail, category, cause)``). The driver consumes the boolean
    **only**: it never re-derives failure genuineness and never inspects
    ``category`` / ``cause`` for control flow — the ID-068 taxonomy is the
    policy's, consumed here, not duplicated (MAS §12.9). ``category`` and
    ``cause`` ride along into the reason and the deterministic fact id.

    The decision table, verbatim:

    ======  ==============================  ====================  ==================
    Row     State                          Facts                Outcome
    ======  ==============================  ====================  ==================
    1       any                            fact ``None``        no-move (§8.8)
    2       any                            ``should_fail`` not  no-move — the policy
                                           ``True`` (incl. the  owns rework/retry/wait
                                           DEVELOPMENT_FAILURE
                                           and BLOCKED verdicts)
    3       terminal (``COMPLETED`` /      ``should_fail``      no-move — already
            ``FAILED``)                    ``is True``          terminal
    4       ``ACTIVE_STATES`` ∪            ``should_fail``      → ``FAILED``
            ``WAITING_FOR_USER``           ``is True``
    ======  ==============================  ====================  ==================

    Args:
        current_state: The workflow's current state.
        failure_fact: The observed should-fail business fact as
            ``(should_fail, category, cause)`` — mirroring the SFP-144
            ``ShouldFailPolicy`` output — or ``None`` when no failure fact has
            been observed at all. ``None`` members inside the tuple mean the
            corresponding field was absent from the observation.

    Returns:
        The resulting workflow state and the immutable :class:`WorkflowDecision`
        recording what was decided and why. Row 4 requests the move through
        the SFP-137 engine with the deterministic fact id in
        ``business_facts_considered``; every other row returns the §8.8 record
        of the *non-move* (same state on both endpoints, ``reason`` naming the
        exact cause). The non-move is returned, never swallowed.

    Raises:
        IllegalTransitionError: if the row-4 move is not in the SFP-137
            transition table (a terminal state is caught earlier by row 3; the
            guard remains the sole authority). Propagated from the table
            guard; the driver never catches or softens it (MAS §8.2).
    """
    if failure_fact is None:
        # Row 1 (§8.8): no observation yet → record the non-move, hold the state.
        return current_state, WorkflowDecision(
            previous_state=current_state,
            resulting_state=current_state,
            reason=_NO_FAILURE_FACT_REASON,
            applied_policy=TERMINAL_FAILURE_POLICY,
            business_facts_considered=("failure-fact:absent",),
            previous_state_name=current_state.name,
            resulting_state_name=current_state.name,
        )

    fact_id = _failure_fact_id(failure_fact)

    if failure_fact[0] is not True:
        # Row 2 (§8.8): the policy said no. Its DEVELOPMENT_FAILURE (ID-068
        # rework loop) and BLOCKED verdicts (retry / wait) land here too — the
        # rework/retry/wait semantics belong to the policy, never to this
        # driver. The boolean is consumed as given; category/cause are not
        # consulted for control flow.
        return current_state, WorkflowDecision(
            previous_state=current_state,
            resulting_state=current_state,
            reason=_SHOULD_NOT_FAIL_REASON,
            applied_policy=TERMINAL_FAILURE_POLICY,
            business_facts_considered=(fact_id,),
            previous_state_name=current_state.name,
            resulting_state_name=current_state.name,
        )

    if current_state in TERMINAL_STATES:
        # Row 3 (§8.8): a terminal workflow never moves — neither COMPLETED
        # nor FAILED is a source of a further move, and the workflow restarts
        # as a *new* workflow instead. Recorded, never an error.
        return current_state, WorkflowDecision(
            previous_state=current_state,
            resulting_state=current_state,
            reason=_ALREADY_TERMINAL_REASON,
            applied_policy=TERMINAL_FAILURE_POLICY,
            business_facts_considered=(fact_id,),
            previous_state_name=current_state.name,
            resulting_state_name=current_state.name,
        )

    # Row 4: the policy said fail — consume the verdict into the FAILED edge
    # through the SFP-137 engine. The engine is the sole legality authority:
    # if the edge is not in TRANSITIONS the IllegalTransitionError propagates
    # (no implicit move, ever). The reason formats the policy's category/cause
    # verbatim; the fact id rides along in business_facts_considered.
    return transition(
        current_state,
        TERMINAL_FAILURE_TARGET,
        reason=_TERMINAL_FAILURE_MOVE_REASON_TEMPLATE.format(
            category=failure_fact[1] if failure_fact[1] else "no-category",
            cause=failure_fact[2] if failure_fact[2] else "no-cause",
        ),
        applied_policy=TERMINAL_FAILURE_POLICY,
        business_facts_considered=(fact_id,),
        aggregate_changes=("tickets.workflow_status",),
    )


__all__ = [
    "CHANGES_REQUESTED_STATUS",
    "CODER_AGENT",
    "CODING_JOB_RUNNING_STATUS",
    "CODING_STAGE_POLICY",
    "CODING_STAGE_SOURCE",
    "CODING_STAGE_TARGET",
    "DEPLOYMENT_FAILED_STATUS",
    "DEPLOYMENT_SUCCEEDED_STATUS",
    "DEPLOY_STAGE_BEGIN_SOURCE",
    "DEPLOY_STAGE_BEGIN_TARGET",
    "DEPLOY_STAGE_FINISH_SOURCE",
    "DEPLOY_STAGE_FINISH_TARGET",
    "DEPLOY_STAGE_POLICY",
    "MERGE_STAGE_POLICY",
    "MERGE_STAGE_SOURCE",
    "MERGE_STAGE_TARGET",
    "MERGE_WAIT_POLICY",
    "MERGE_WAIT_SOURCE",
    "MERGE_WAIT_TARGET",
    "PLANNER_AGENT",
    "TERMINAL_FAILURE_POLICY",
    "TERMINAL_FAILURE_TARGET",
    "USER_DECISION_APPROVE",
    "USER_DECISION_ANSWER",
    "USER_DECISION_POLICY",
    "USER_DECISION_REJECT",
    "USER_DECISION_SOURCE",
    "REVIEW_STAGE_POLICY",
    "REVIEW_STAGE_SOURCE",
    "REVIEW_STAGE_TARGET",
    "REWORK_POLICY",
    "REWORK_SOURCE",
    "REWORK_TARGET",
    "approval_fact_granted",
    "changes_requested_review_fact",
    "coding_job_started_fact",
    "deploy_outcome_recognized",
    "drive_coding_stage",
    "drive_deploy_stage",
    "drive_merge_stage",
    "drive_merge_wait",
    "drive_review_stage",
    "drive_rework_loop",
    "drive_spec_stage",
    "drive_terminal_failure",
    "drive_user_decision",
    "pr_created_fact",
    "spec_stage_fact_is_successful",
    "user_decision_recognized",
]
