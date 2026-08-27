"""The coding-start policy (MAS §8.14, SFP-143).

Decides the core loop's first edge — ``READY_FOR_CODING → CODING_IN_PROGRESS``
— from the :class:`~.facts.CodingStartFact` business facts: the ticket is
admitted for coding and a coder slot is free. Every other case is a recorded
no-transition (MAS §8.8) naming the specific missing precondition.

Grounded in:
- MAS §8.14 — a policy is a deterministic, side-effect-free function of the
  current workflow state and observed business facts that decides which
  transition applies and which commands are emitted.
- MAS §8.8 — "no transition" is an observable, recorded outcome, never an
  exception and never a silent skip.
- MAS §8.6 — commands never modify workflow state; the policy only *carries*
  the command name as data.
- MAS §12.9 — the command name is the landed ``ExecuteCodingJob`` payload
  class from ``sfp_contracts.commands``; none is invented here.
- ID-013 — enums serialize as plain strings in any serialized field.
- AP-011 — purity: no clock, no randomness, no I/O, no bus.

The policy implements the SFP-142
:class:`~orchestrator.domain.workflow.policy_engine.WorkflowPolicy` protocol
and is consumed through
:func:`~orchestrator.domain.workflow.policy_engine.evaluate`, which validates
the decided target against the SFP-137 table. This module never applies a
transition itself.
"""

from __future__ import annotations

from collections.abc import Sequence

from sfp_contracts.commands import ExecuteCodingJob

from orchestrator.domain.workflow.policies.facts import (
    CODING_START_FACT_KIND,
    CodingStartFact,
)
from orchestrator.domain.workflow.policy_engine import PolicyDecision
from orchestrator.domain.workflow.states import WorkflowState

#: The policy's recorded identifier (MAS §8.5 ``applied_policy``).
POLICY_NAME = "coding-start"

#: The single edge this policy owns (§8.4 order; legality itself lives in the
#: SFP-137 ``TRANSITIONS`` table and is never re-implemented here).
SOURCE_STATE = WorkflowState.READY_FOR_CODING
TARGET_STATE = WorkflowState.CODING_IN_PROGRESS

#: The landed command this policy references (as a name only, §8.6). The
#: payload class *is* the command name under the landed SFP-219 shape.
COMMAND_NAME = ExecuteCodingJob.__name__

#: Reasons recorded on the moves and non-moves (§8.5 / §8.8).
MOVE_REASON = "admitted coding-start fact and capacity available: start coding"
WRONG_STATE_REASON = (
    "not READY_FOR_CODING: the coding-start policy applies only to tickets ready for coding"
)
NOT_ADMITTED_REASON = "coding-start fact not admitted: no valid ExecuteCodingJob-style request"
NO_CAPACITY_REASON = "no coding capacity available: the workflow stays at this stage"
NO_FACT_REASON = "no coding-start fact observed: the workflow stays at this stage"

#: Prefix of the fact strings this policy consumes (see
#: :meth:`CodingStartFact.to_fact_strings`).
_FACT_PREFIX = f"{CODING_START_FACT_KIND}:"

#: The deterministic rendering of "admitted" / "capacity" fact strings, used to
#: lift engine-level fact strings back into the typed model.
_ADMITTED_TRUE = f"{_FACT_PREFIX}start_request_admitted:True"
_CAPACITY_TRUE = f"{_FACT_PREFIX}capacity_available:True"
_ADMITTED_FALSE = f"{_FACT_PREFIX}start_request_admitted:False"
_CAPACITY_FALSE = f"{_FACT_PREFIX}capacity_available:False"


class CodingStartPolicy:
    """Decide the coding-start edge from the coding-start business facts.

    Pure and deterministic (AP-011): the same ``(state, facts)`` always yield
    an equal :class:`~orchestrator.domain.workflow.policy_engine.PolicyDecision`.
    No bus, no clock, no I/O; the ``ExecuteCodingJob`` command rides along as a
    name only (§8.6) and is never built, dispatched, or executed here.

    The protocol's ``business_facts`` are the engine's fact *strings*; a typed
    :class:`~.facts.CodingStartFact` is lifted from them via :meth:`parse_fact`
    (the same rendering :meth:`CodingStartFact.to_fact_strings` produces), so
    callers may pass either form and get the same verdict.
    """

    #: Convenience alias so callers can construct the fact without a second import.
    fact_type = CodingStartFact

    def decide(
        self,
        current_state: WorkflowState,
        business_facts: Sequence[str],
    ) -> PolicyDecision:
        """Render this policy's verdict for the given state and facts.

        Routing (all non-moves are recorded §8.8 values, never exceptions):

        - ``READY_FOR_CODING`` + admitted fact + capacity → transition to
          ``CODING_IN_PROGRESS``, referencing the ``ExecuteCodingJob`` command
          name;
        - any other state → no-transition (wrong stage);
        - no coding-start fact at all → no-transition (absent fact);
        - fact present but not admitted → no-transition (specific reason);
        - fact admitted but no capacity → no-transition (specific reason).
        """
        if current_state is not SOURCE_STATE:
            return PolicyDecision.no_transition_verdict(reason=WRONG_STATE_REASON)

        fact = self.parse_fact(business_facts)
        if fact is None:
            return PolicyDecision.no_transition_verdict(reason=NO_FACT_REASON)
        if not fact.start_request_admitted:
            return PolicyDecision.no_transition_verdict(reason=NOT_ADMITTED_REASON)
        if not fact.capacity_available:
            return PolicyDecision.no_transition_verdict(reason=NO_CAPACITY_REASON)

        return PolicyDecision.transition_verdict(
            TARGET_STATE,
            reason=MOVE_REASON,
            command_names=(COMMAND_NAME,),
        )

    @staticmethod
    def parse_fact(business_facts: Sequence[str]) -> CodingStartFact | None:
        """Lift the typed fact from the engine's fact strings — deterministic.

        Returns ``None`` when no coding-start fact is present. Recognition is
        total and order-independent: the fact strings are matched on their
        exact deterministic renderings, so unknown facts are ignored and the
        same input always lifts to the same model.
        """
        observed = frozenset(business_facts)
        if not any(fact.startswith(_FACT_PREFIX) for fact in observed):
            return None
        return CodingStartFact(
            start_request_admitted=_ADMITTED_TRUE in observed,
            capacity_available=_CAPACITY_TRUE in observed,
        )


__all__ = [
    "COMMAND_NAME",
    "POLICY_NAME",
    "SOURCE_STATE",
    "TARGET_STATE",
    "CodingStartPolicy",
]
