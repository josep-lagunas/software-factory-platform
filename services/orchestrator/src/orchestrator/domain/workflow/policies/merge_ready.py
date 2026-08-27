"""The merge-readiness policy (MAS §8.14, SFP-143).

Decides the merge stage's edge — ``READY_FOR_MERGE → MERGING`` — from the
:class:`~.facts.MergeReadyFact` business facts: the PR's review is APPROVED
for the **current head** and every CI / validation gate is green. Any missing
fact is a recorded no-transition (§8.8) naming exactly which fact is missing.

Grounded in:
- MAS §8.14 — deterministic, side-effect-free policy functions of (state,
  observed facts) deciding the transition and the commands.
- MAS §8.8 — "no transition" is an observable, recorded outcome.
- MAS §8.6 — commands never modify workflow state; the policy only *carries*
  the command name as data.
- ID-072 — the merge *decision* is the Orchestrator's, expressed by emitting
  ``RequestMerge``; this policy only decides that the workflow may move to
  ``MERGING`` and references the command by name. The merge *execution* is the
  Workspace Worker's (never here).
- MAS §12.9 — the command name is the landed ``RequestMerge`` payload class
  from ``sfp_contracts.commands``; none is invented here.
- ID-013 / AP-011 — plain-string serialization; purity.

Implements the SFP-142
:class:`~orchestrator.domain.workflow.policy_engine.WorkflowPolicy` protocol;
consumed through
:func:`~orchestrator.domain.workflow.policy_engine.evaluate`, which validates
the decided target against the SFP-137 table. This module never applies a
transition, never touches the git provider, and never merges anything.
"""

from __future__ import annotations

from collections.abc import Sequence

from sfp_contracts.commands import RequestMerge

from orchestrator.domain.workflow.policies.facts import (
    MERGE_READY_FACT_KIND,
    MergeReadyFact,
)
from orchestrator.domain.workflow.policy_engine import PolicyDecision
from orchestrator.domain.workflow.states import WorkflowState

#: The policy's recorded identifier (MAS §8.5 ``applied_policy``).
POLICY_NAME = "merge-ready"

#: The single edge this policy owns (§8.4 order; legality lives in the SFP-137
#: ``TRANSITIONS`` table and is never re-implemented here).
SOURCE_STATE = WorkflowState.READY_FOR_MERGE
TARGET_STATE = WorkflowState.MERGING

#: The landed command this policy references (as a name only, §8.6). The
#: payload class *is* the command name under the landed SFP-219 shape.
COMMAND_NAME = RequestMerge.__name__

#: Reasons recorded on the moves and non-moves (§8.5 / §8.8).
MOVE_REASON = "PR review approved for the current head and all gates green: merge"
WRONG_STATE_REASON = "not READY_FOR_MERGE: the merge-ready policy applies only after approval"
NO_FACT_REASON = "no merge-ready fact observed: the workflow stays at this stage"

#: Names for each missing fact, so the recorded reason names *exactly* which
#: fact is unsatisfied. Keyed by the fact's field name.
_MISSING_PR_APPROVAL = "pr_review_approved_for_head"
_MISSING_CI_GREEN = "ci_gates_green"

#: The deterministic renderings of the merge-ready fact strings (see
#: :meth:`MergeReadyFact.to_fact_strings`).
_FACT_PREFIX = f"{MERGE_READY_FACT_KIND}:"
_PR_APPROVED_TRUE = f"{_FACT_PREFIX}{_MISSING_PR_APPROVAL}:True"
_CI_GREEN_TRUE = f"{_FACT_PREFIX}{_MISSING_CI_GREEN}:True"
_PR_APPROVED_FALSE = f"{_FACT_PREFIX}{_MISSING_PR_APPROVAL}:False"
_CI_GREEN_FALSE = f"{_FACT_PREFIX}{_MISSING_CI_GREEN}:False"

#: Missing-fact reason templates. A single missing fact names that fact; two
#: missing facts name both, in the fact model's declaration order (so the
#: reason is a deterministic function of the input, AP-011).
_MISSING_ONE_TEMPLATE = "merge not ready: missing fact {field}"
_MISSING_BOTH_TEMPLATE = "merge not ready: missing facts {first} and {second}"


class MergeReadyPolicy:
    """Decide the merge edge from the merge-readiness business facts.

    Pure and deterministic (AP-011): the same ``(state, facts)`` always yield
    an equal :class:`~orchestrator.domain.workflow.policy_engine.PolicyDecision`.
    No bus, no clock, no I/O; the ``RequestMerge`` command rides along as a
    name only (§8.6) and is never built, dispatched, or executed here —
    deciding to merge and merging are different things (ID-072).
    """

    #: Convenience alias so callers can construct the fact without a second import.
    fact_type = MergeReadyFact

    def decide(
        self,
        current_state: WorkflowState,
        business_facts: Sequence[str],
    ) -> PolicyDecision:
        """Render this policy's verdict for the given state and facts.

        Routing (all non-moves are recorded §8.8 values, never exceptions):

        - ``READY_FOR_MERGE`` + head-approved review + green gates →
          transition to ``MERGING``, referencing the ``RequestMerge`` command
          name;
        - any other state → no-transition (wrong stage);
        - no merge-ready fact → no-transition (absent fact);
        - some fact(s) observed but unsatisfied → no-transition naming exactly
          which fact(s) are missing.
        """
        if current_state is not SOURCE_STATE:
            return PolicyDecision.no_transition_verdict(reason=WRONG_STATE_REASON)

        observed = frozenset(business_facts)
        if not any(fact.startswith(_FACT_PREFIX) for fact in observed):
            return PolicyDecision.no_transition_verdict(reason=NO_FACT_REASON)

        missing = self._missing_fields(observed)
        if missing:
            return PolicyDecision.no_transition_verdict(reason=self._missing_reason(missing))

        return PolicyDecision.transition_verdict(
            TARGET_STATE,
            reason=MOVE_REASON,
            command_names=(COMMAND_NAME,),
        )

    @staticmethod
    def _missing_fields(observed: frozenset[str]) -> tuple[str, ...]:
        """Name the unsatisfied facts, in the fact model's declaration order."""
        missing: list[str] = []
        if _PR_APPROVED_TRUE not in observed:
            missing.append(_MISSING_PR_APPROVAL)
        if _CI_GREEN_TRUE not in observed:
            missing.append(_MISSING_CI_GREEN)
        return tuple(missing)

    @staticmethod
    def _missing_reason(missing: Sequence[str]) -> str:
        """Render the missing-fact reason deterministically."""
        if len(missing) == 1:
            return _MISSING_ONE_TEMPLATE.format(field=missing[0])
        return _MISSING_BOTH_TEMPLATE.format(first=missing[0], second=missing[1])

    @staticmethod
    def parse_fact(business_facts: Sequence[str]) -> MergeReadyFact | None:
        """Lift the typed fact from the engine's fact strings — deterministic.

        Returns ``None`` when no merge-ready fact is present. Recognition is
        total and order-independent: the exact deterministic renderings are
        matched, so unknown facts are ignored.
        """
        observed = frozenset(business_facts)
        if not any(fact.startswith(_FACT_PREFIX) for fact in observed):
            return None
        return MergeReadyFact(
            pr_review_approved_for_head=_PR_APPROVED_TRUE in observed,
            ci_gates_green=_CI_GREEN_TRUE in observed,
        )


__all__ = [
    "COMMAND_NAME",
    "POLICY_NAME",
    "SOURCE_STATE",
    "TARGET_STATE",
    "MergeReadyPolicy",
]
