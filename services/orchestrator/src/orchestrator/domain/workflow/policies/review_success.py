"""The review-outcome policy (MAS §8.14, ID-068, SFP-143).

Decides the review stage's two edges from the
:class:`~.facts.ReviewFact` business facts:

- ``APPROVED`` — ``REVIEW_IN_PROGRESS → READY_FOR_MERGE``;
- ``CHANGES_REQUESTED`` — ``REVIEW_IN_PROGRESS → CODING_IN_PROGRESS``, the
  ID-068 rework loop: **normal workflow progression**, never a failure and
  never an escalation;
- ``BLOCKED`` / ``NEEDS_HUMAN_DECISION`` — a recorded no-transition (§8.8)
  whose reason names the verdict and which references the escalation command
  name. The policy **decides only**: driving the workflow into
  ``WAITING_FOR_USER`` is SFP-141's concern (doc-124), and this policy never
  requests it.

Grounded in:
- MAS §8.14 — deterministic, side-effect-free policy functions of (state,
  observed facts) deciding the transition and the commands.
- MAS §8.8 — "no transition" is an observable, recorded outcome.
- ID-068 — a ``CHANGES_REQUESTED`` review is the expected coder↔reviewer
  quality loop: it drives rework and never enters ``FAILED`` or any escalation
  path. There is no code path in this module that could request either.
- MAS §12.9 — the escalation command name is the landed ``RequestUserInput``
  payload class from ``sfp_contracts.commands`` (the catalogue's only
  ask-the-user command; the canonical term is User, MAS §6.11); none is
  invented here.
- ID-013 / AP-011 — plain-string serialization; purity.

Implements the SFP-142
:class:`~orchestrator.domain.workflow.policy_engine.WorkflowPolicy` protocol;
consumed through
:func:`~orchestrator.domain.workflow.policy_engine.evaluate`.
"""

from __future__ import annotations

from collections.abc import Sequence

from sfp_contracts.commands import RequestUserInput

from orchestrator.domain.workflow.policies.facts import REVIEW_FACT_KIND, ReviewFact, ReviewStatus
from orchestrator.domain.workflow.policy_engine import PolicyDecision
from orchestrator.domain.workflow.states import WorkflowState

#: The policy's recorded identifier (MAS §8.5 ``applied_policy``).
POLICY_NAME = "review-success"

#: The edges this policy owns (§8.4 order; legality lives in the SFP-137
#: ``TRANSITIONS`` table and is never re-implemented here).
SOURCE_STATE = WorkflowState.REVIEW_IN_PROGRESS
MERGE_TARGET = WorkflowState.READY_FOR_MERGE
#: The ID-068 rework edge — normal progression, never FAILED.
REWORK_TARGET = WorkflowState.CODING_IN_PROGRESS

#: The landed escalation command this policy references on a blocked /
#: needs-human-decision verdict (as a name only, §8.6).
ESCALATION_COMMAND_NAME = RequestUserInput.__name__

#: Reasons recorded on the moves and non-moves (§8.5 / §8.8).
APPROVED_REASON = "review approved: ready for merge"
REWORK_REASON = "changes-requested review: rework is normal progression (ID-068)"
BLOCKED_REASON = "review blocked: escalation to the user (RequestUserInput); the workflow stays"
NEEDS_HUMAN_DECISION_REASON = (
    "review needs a human decision: escalation to the user (RequestUserInput); the workflow stays"
)
WRONG_STATE_REASON = "not REVIEW_IN_PROGRESS: the review-outcome policy applies only during review"
NO_FACT_REASON = "no review fact observed: the workflow stays at this stage"

#: The single deterministic rendering of a review fact string (see
#: :meth:`ReviewFact.to_fact_strings`).
_FACT_TEMPLATE = f"{REVIEW_FACT_KIND}:review_status:"


class ReviewSuccessPolicy:
    """Decide the review outcome's routing from the review business facts.

    Pure and deterministic (AP-011): the same ``(state, facts)`` always yield
    an equal :class:`~orchestrator.domain.workflow.policy_engine.PolicyDecision`.
    No bus, no clock, no I/O; commands ride along as names only (§8.6) and are
    never built, dispatched, or executed here.

    ``CHANGES_REQUESTED`` never produces ``FAILED`` and never references the
    escalation command — the only targets this policy can request are
    :data:`MERGE_TARGET` and :data:`REWORK_TARGET` (ID-068).
    """

    #: Convenience alias so callers can construct the fact without a second import.
    fact_type = ReviewFact

    def decide(
        self,
        current_state: WorkflowState,
        business_facts: Sequence[str],
    ) -> PolicyDecision:
        """Render this policy's verdict for the given state and facts.

        Routing (all non-moves are recorded §8.8 values, never exceptions):

        - any state other than ``REVIEW_IN_PROGRESS`` → no-transition (wrong
          stage), regardless of the verdict;
        - ``APPROVED`` → transition to ``READY_FOR_MERGE`` (no command — the
          merge-stage policy owns the ``RequestMerge`` reference);
        - ``CHANGES_REQUESTED`` → transition to ``CODING_IN_PROGRESS``,
          the ID-068 rework loop (no FAILED, no escalation, ever);
        - ``BLOCKED`` / ``NEEDS_HUMAN_DECISION`` → no-transition with the
          verdict named in the reason and the escalation command name
          recorded (decide only; SFP-141 drives the user wait);
        - no review fact → no-transition (absent fact).
        """
        if current_state is not SOURCE_STATE:
            return PolicyDecision.no_transition_verdict(reason=WRONG_STATE_REASON)

        status = self.parse_review_status(business_facts)
        if status is None:
            return PolicyDecision.no_transition_verdict(reason=NO_FACT_REASON)

        if status is ReviewStatus.APPROVED:
            return PolicyDecision.transition_verdict(
                MERGE_TARGET,
                reason=APPROVED_REASON,
            )

        if status is ReviewStatus.CHANGES_REQUESTED:
            # ID-068: normal progression. The escalation command is never
            # referenced on this path, and FAILED is never the target.
            return PolicyDecision.transition_verdict(
                REWORK_TARGET,
                reason=REWORK_REASON,
            )

        # BLOCKED / NEEDS_HUMAN_DECISION: record the non-move and reference
        # the escalation command by name. The policy does not drive
        # WAITING_FOR_USER (SFP-141 / doc-124 owns that).
        reason = BLOCKED_REASON if status is ReviewStatus.BLOCKED else NEEDS_HUMAN_DECISION_REASON
        # A no-transition verdict may still carry command names (the validator
        # only excludes a *target state*): the escalation is recorded as a
        # reference alongside the non-move.
        return PolicyDecision(
            target_state=None,
            no_transition=True,
            reason=reason,
            command_names=(ESCALATION_COMMAND_NAME,),
        )

    @staticmethod
    def parse_review_status(facts: Sequence[str]) -> ReviewStatus | None:
        """Lift the review verdict from the engine's fact strings — deterministic.

        Returns ``None`` when no review fact is present. Recognition is total
        and order-independent: the exact deterministic renderings are matched,
        so unknown facts are ignored.
        """
        observed = frozenset(facts)
        for status in ReviewStatus:
            if f"{_FACT_TEMPLATE}{status.value}" in observed:
                return status
        return None

    @staticmethod
    def parse_fact(facts: Sequence[str]) -> ReviewFact | None:
        """Lift the typed :class:`~.facts.ReviewFact`, or ``None`` if absent."""
        status = ReviewSuccessPolicy.parse_review_status(facts)
        return None if status is None else ReviewFact(review_status=status)


__all__ = [
    "ESCALATION_COMMAND_NAME",
    "MERGE_TARGET",
    "POLICY_NAME",
    "REWORK_TARGET",
    "SOURCE_STATE",
    "ReviewSuccessPolicy",
]
