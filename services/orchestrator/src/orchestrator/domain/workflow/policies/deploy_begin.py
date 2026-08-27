"""The deploy-begin policy (MAS §8.14, SFP-144).

Decides the delivery tail's edge — ``MERGING → DEPLOYING`` — from the
:class:`~.facts.DeployBeginFact` business facts: the merge has completed and a
deploy target ref is set. The exhaustive decision table (from the PRSpec),
evaluated in this order:

====  ==================  ===================  ==========================  =========================
row   current state       merge_completed      deploy_target_ref          decision
====  ==================  ===================  ==========================  =========================
1     any state ≠ MERGING —                    —                          recorded no-move (stage)
2     MERGING             False                —                          recorded no-move (merge)
3     MERGING             True                 absent / empty             recorded no-move (no ref)
4     MERGING             True                 present (non-empty)        move to DEPLOYING
====  ==================  ===================  ==========================  =========================
"""

from __future__ import annotations

from collections.abc import Sequence

from sfp_contracts.commands import NotifyUser

from orchestrator.domain.workflow.policies.facts import (
    DEPLOY_BEGIN_FACT_KIND,
    DeployBeginFact,
)
from orchestrator.domain.workflow.policy_engine import PolicyDecision
from orchestrator.domain.workflow.states import WorkflowState

#: The policy's recorded identifier (MAS §8.5 ``applied_policy``).
POLICY_NAME = "deploy-begin"

#: The single edge this policy owns (§8.4 order; legality itself lives in the
#: SFP-137 ``TRANSITIONS`` table and is never re-implemented here).
SOURCE_STATE = WorkflowState.MERGING
TARGET_STATE = WorkflowState.DEPLOYING

#: The landed command this policy references (as a name only, §8.6). The
#: payload class *is* the command name under the landed SFP-219 shape.
COMMAND_NAME = NotifyUser.__name__

#: Reasons recorded on the move and non-moves (§8.5 / §8.8).
MOVE_REASON = "merge completed and a deploy target ref is set: begin deployment"
WRONG_STATE_REASON = (
    "not MERGING: the deploy-begin policy applies only while merging, before deploy"
)
MERGE_NOT_COMPLETED_REASON = "merge not completed: deployment waits for the merge"
NO_TARGET_REF_REASON = "no deploy target ref set: deployment cannot begin without a target ref"
NO_FACT_REASON = "no deploy-begin fact observed: the workflow stays at this stage"

#: The deterministic renderings of the deploy-begin fact strings (see
#: :meth:`DeployBeginFact.to_fact_strings`).
_FACT_PREFIX = f"{DEPLOY_BEGIN_FACT_KIND}:"
_MERGE_TRUE = f"{_FACT_PREFIX}merge_completed:True"
_REF_FIELD = "deploy_target_ref"
_REF_PREFIX = f"{_FACT_PREFIX}{_REF_FIELD}:"


class DeployBeginPolicy:
    """Decide the deploy-begin edge from the merge/deploy business facts.

    Pure and deterministic (AP-011): the same ``(state, facts)`` always yield
    an equal :class:`~orchestrator.domain.workflow.policy_engine.PolicyDecision`.
    No bus, no clock, no I/O; the ``NotifyUser`` command rides along as a name
    only (§8.6) and is never built, dispatched, or executed here.

    The ``deploy_target_ref`` is carried as data: the policy asks only whether
    one is present (non-empty); interpreting or resolving the ref is the
    deploy executor's concern, never this module's.
    """

    #: Convenience alias so callers can construct the fact without a second import.
    fact_type = DeployBeginFact

    def decide(
        self,
        current_state: WorkflowState,
        business_facts: Sequence[str],
    ) -> PolicyDecision:
        """Render this policy's verdict for the given state and facts.

        Routing (the PRSpec's 4-row table, in order; all non-moves are
        recorded §8.8 values, never exceptions):

        - row 1 — any state other than ``MERGING`` → no-transition (wrong
          stage), regardless of the facts;
        - row 2 — ``MERGING`` but the merge has not completed →
          no-transition (merge pending);
        - row 3 — ``MERGING``, merge completed, but no (or an empty) deploy
          target ref → no-transition (no target);
        - row 4 — ``MERGING``, merge completed, target ref present →
          transition to ``DEPLOYING``, referencing the ``NotifyUser`` command
          name;
        - no deploy-begin fact → no-transition (absent fact).
        """
        if current_state is not SOURCE_STATE:
            return PolicyDecision.no_transition_verdict(reason=WRONG_STATE_REASON)

        observed = frozenset(business_facts)
        if not any(fact.startswith(_FACT_PREFIX) for fact in observed):
            return PolicyDecision.no_transition_verdict(reason=NO_FACT_REASON)
        if _MERGE_TRUE not in observed:
            return PolicyDecision.no_transition_verdict(reason=MERGE_NOT_COMPLETED_REASON)
        if not self._target_ref(observed):
            return PolicyDecision.no_transition_verdict(reason=NO_TARGET_REF_REASON)

        return PolicyDecision.transition_verdict(
            TARGET_STATE,
            reason=MOVE_REASON,
            command_names=(COMMAND_NAME,),
        )

    @staticmethod
    def _target_ref(observed: frozenset[str]) -> str | None:
        """Return the observed deploy target ref, or ``None`` when not set.

        The ref renders as ``<kind>:deploy_target_ref:<ref>``; an empty ref
        renders as the bare prefix and counts as *not set*. Deterministic and
        order-independent: at most one rendering per field can be present, so
        the first matching fact is the only matching fact.
        """
        for fact in sorted(observed):
            if fact.startswith(_REF_PREFIX) and fact != _REF_PREFIX:
                return fact.removeprefix(_REF_PREFIX)
        return None

    @staticmethod
    def parse_fact(business_facts: Sequence[str]) -> DeployBeginFact | None:
        """Lift the typed fact from the engine's fact strings — deterministic.

        Returns ``None`` when no deploy-begin fact is present. Recognition is
        total and order-independent: the exact deterministic renderings are
        matched, so unknown facts are ignored.
        """
        observed = frozenset(business_facts)
        if not any(fact.startswith(_FACT_PREFIX) for fact in observed):
            return None
        target_ref = DeployBeginPolicy._target_ref(observed)
        return DeployBeginFact(
            merge_completed=_MERGE_TRUE in observed,
            deploy_target_ref=target_ref if target_ref is not None else "",
        )


__all__ = [
    "COMMAND_NAME",
    "POLICY_NAME",
    "SOURCE_STATE",
    "TARGET_STATE",
    "DeployBeginPolicy",
]
