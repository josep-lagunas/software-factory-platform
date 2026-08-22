"""Stage-transition drivers for the coding/review stages (MAS §8.4–8.8).

Thin, **pure** drivers that advance a ticket workflow through the coding and
review stages, delegating every actual move to the SFP-137 engine
(:func:`orchestrator.domain.workflow.state_machine.transition`), which validates
against :data:`TRANSITIONS` and returns the immutable
:class:`WorkflowDecision` (§8.5). Three edges are driven here:

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

On a failed, absent, or non-matching fact there is **no** transition, and the
non-move is recorded as a business fact (§8.8) rather than swallowed —
including an ``APPROVED`` review fact, which does *not* loop back to coding:
the merge stage it selects is SFP-140's concern, so it is recorded as a
non-move here.

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

from orchestrator.domain.workflow.state_machine import WorkflowDecision, transition
from orchestrator.domain.workflow.states import WorkflowState

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

#: The trivial fixed policies these drivers apply (SFP-142 owns real policy
#: selection; a fixed rule is acceptable now per the PRSpec notes).
#: Recorded verbatim on every decision this module produces.
SPEC_STAGE_POLICY = "spec-stage-successful-plan"
CODING_STAGE_POLICY = "coding-stage-job-started"
REVIEW_STAGE_POLICY = "review-stage-pr-opened"
REWORK_POLICY = "review-stage-changes-requested-rework"

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


__all__ = [
    "CHANGES_REQUESTED_STATUS",
    "CODER_AGENT",
    "CODING_JOB_RUNNING_STATUS",
    "CODING_STAGE_POLICY",
    "CODING_STAGE_SOURCE",
    "CODING_STAGE_TARGET",
    "PLANNER_AGENT",
    "REVIEW_STAGE_POLICY",
    "REVIEW_STAGE_SOURCE",
    "REVIEW_STAGE_TARGET",
    "REWORK_POLICY",
    "REWORK_SOURCE",
    "REWORK_TARGET",
    "changes_requested_review_fact",
    "coding_job_started_fact",
    "drive_coding_stage",
    "drive_review_stage",
    "drive_rework_loop",
    "drive_spec_stage",
    "pr_created_fact",
    "spec_stage_fact_is_successful",
]
