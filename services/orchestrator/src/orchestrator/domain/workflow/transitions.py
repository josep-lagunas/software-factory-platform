"""The Ticket→PR-spec stage-transition driver (MAS §8.4–8.8, SFP-138).

A thin, **pure** driver that advances a ticket workflow from
``READY_FOR_PR_SPECIFICATION`` to ``READY_FOR_CODING`` when a *successful-plan*
business fact is observed, delegating the actual move to the SFP-137 engine
(:func:`orchestrator.domain.workflow.state_machine.transition`), which validates
against :data:`TRANSITIONS` and returns the immutable
:class:`WorkflowDecision` (§8.5). On a failed or absent plan fact there is **no**
transition, and the non-move is recorded as a business fact (§8.8) rather than
swallowed.

Grounded in:
- MAS §8.5 — every significant transition produces an immutable
  ``WorkflowDecision`` recording why, applied policy, facts, aggregate changes,
  commands emitted, previous/resulting state.
- MAS §8.6 — the workflow advances only on *events / user decisions*; commands
  never modify workflow state. The driver's only trigger is the plan fact
  (an ``AgentStatus.SUCCESS`` ``PRSpecificationsUpdated``-style observation).
- MAS §8.7 — outputs are deterministic; the driver is a pure function of its
  inputs (AP-011: no I/O, no clock, no randomness).
- MAS §8.8 — failures are business facts too: the decision *not* to move is
  constructed and returned, never swallowed and never silently escalated.
- MAS §8.2 / SFP-137 — no implicit transitions: an illegal move raises
  :class:`~orchestrator.domain.workflow.state_machine.IllegalTransitionError`
  straight from the table guard; the driver never catches or softens it.
- ID-013 — enums serialize as plain strings in any serialized field (the
  ``WorkflowDecision`` ``*_name`` companions).
- SFP-142 — the policy engine that will own real policy selection is a later
  ticket; this driver carries the one trivial fixed rule it needs (the
  successful-plan gate) as module-level data, not as a policy abstraction.

The driver implements the SFP-137 ``TransitionDriver`` seam shape: it observes
the plan-stage facts and requests the decided move through the engine. It adds
no workflow states, duplicates no transition-table data, and performs no bus
I/O — the bus remains an injected seam handled by
:class:`~orchestrator.domain.workflow.state_machine.WorkflowTransitionPublisher`
outside this module.
"""

from __future__ import annotations

from orchestrator.domain.workflow.state_machine import WorkflowDecision, transition
from orchestrator.domain.workflow.states import WorkflowState

#: The single stage edge this driver owns (§8.4 order: planning completed → the
#: ticket is ready for coding). Taken from the SFP-137 table's semantics; the
#: legality check itself lives in ``TRANSITIONS`` and is never re-implemented
#: here (zero duplication).
SPEC_STAGE_SOURCE = WorkflowState.READY_FOR_PR_SPECIFICATION
SPEC_STAGE_TARGET = WorkflowState.READY_FOR_CODING

#: The trivial fixed policy this driver applies (SFP-142 owns real policy
#: selection later; a fixed rule is acceptable now per the PRSpec notes).
#: Recorded verbatim on every decision this module produces.
SPEC_STAGE_POLICY = "spec-stage-successful-plan"

#: The agent whose output this stage consumes. The canonical agent identifier
#: (workspace-worker planner agent) — kept as data so the fact-type match stays
#: a plain comparison, not a class-identity check.
PLANNER_AGENT = "planner"

#: Reason recorded on the move (§8.5 ``reason``).
_MOVE_REASON = "successful plan observed: planner produced validated PR-specs"

#: Reasons recorded on the non-moves (§8.8 — the non-move is a recorded
#: business fact, not a swallowed branch).
_NO_FACT_REASON = "no plan fact observed: the workflow stays at this stage"
_FAILED_FACT_REASON = "plan fact not successful: the workflow stays at this stage"


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
        reason=_MOVE_REASON,
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


__all__ = [
    "PLANNER_AGENT",
    "SPEC_STAGE_POLICY",
    "SPEC_STAGE_SOURCE",
    "SPEC_STAGE_TARGET",
    "drive_spec_stage",
    "spec_stage_fact_is_successful",
]
