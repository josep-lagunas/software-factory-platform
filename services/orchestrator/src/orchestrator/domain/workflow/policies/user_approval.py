"""The user-approval policy (MAS §8.14, ID-024/ID-067, SFP-144).

Decides the merge stage's approval edge — ``MERGING → WAITING_FOR_USER`` —
from the :class:`~.facts.UserApprovalFact` business facts: the PR-spec's
validation profile. Whether a human must approve the merge is a function of
that tier *alone* (ID-024): every tier above ``LEVEL_1_INTERNAL`` requires a
human approval before the merge proceeds, and no ad-hoc boolean may stand in
for the profile. The decision table (from the PRSpec):

===============  ============================  =========================
current profile  decision                     reason
===============  ============================  =========================
LEVEL_1_INTERNAL  recorded no-move             internal: auto-merge path
LEVEL_2_BACKEND  move to WAITING_FOR_USER      tier requires approval
LEVEL_3_USER_     move to WAITING_FOR_USER      tier requires approval
  FACING
LEVEL_4_HIGH_RISK move to WAITING_FOR_USER      tier requires approval
absent / unknown  recorded no-move (fail-closed)  profile not resolvable
===============  ============================  =========================

The fail-closed row is deliberate: when the profile cannot be resolved to a
landed :class:`~sfp_contracts.validation.profiles.ValidationProfile` member,
the policy declines to move rather than guessing — an unresolvable
authorization input must never default to "no human needed".

Grounded in:
- MAS §8.14 — deterministic, side-effect-free policy functions of (state,
  observed facts) deciding the transition and the commands.
- MAS §8.8 — "no transition" is an observable, recorded outcome, never an
  exception and never a silent skip.
- MAS §8.6 — commands never modify workflow state; the policy only *carries*
  the command name as data.
- ID-024 — only ``LEVEL_1_INTERNAL`` is auto-merge eligible; every other tier
  requires a human approval before merge. Deciding the requirement is this
  policy's job; collecting the approval is the CONFIRM flow's (ID-069).
- ID-067 — the tier is decided at PR-spec time and carried on the spec, so
  the policy reads a *landed* profile, never re-derives it.
- MAS §12.9 — the command name is the landed ``RequestUserInput`` payload
  class from ``sfp_contracts.commands``; none is invented here.
- ID-013 / AP-011 — plain-string serialization; purity.

Out of scope (per the PRSpec): the ``APPROVE → READY_FOR_MERGE`` /
``REJECT → FAILED`` continuation belongs to the merge driver (ID-069), not to
this policy — this policy only parks the workflow for the decision.

Implements the SFP-142
:class:`~orchestrator.domain.workflow.policy_engine.WorkflowPolicy` protocol;
consumed through
:func:`~orchestrator.domain.workflow.policy_engine.evaluate`, which validates
the decided target against the SFP-137 table. This module never applies a
transition itself.
"""

from __future__ import annotations

from collections.abc import Sequence

from sfp_contracts.commands import RequestUserInput
from sfp_contracts.validation.profiles import (
    REQUIRES_HUMAN_APPROVAL,
    ValidationProfile,
)

from orchestrator.domain.workflow.policies.facts import (
    USER_APPROVAL_FACT_KIND,
    UserApprovalFact,
)
from orchestrator.domain.workflow.policy_engine import PolicyDecision
from orchestrator.domain.workflow.states import WorkflowState

#: The policy's recorded identifier (MAS §8.5 ``applied_policy``).
POLICY_NAME = "user-approval"

#: The single edge this policy owns (§8.4 order; legality itself lives in the
#: SFP-137 ``TRANSITIONS`` table and is never re-implemented here).
SOURCE_STATE = WorkflowState.MERGING
TARGET_STATE = WorkflowState.WAITING_FOR_USER

#: The landed command this policy references (as a name only, §8.6). The
#: payload class *is* the command name under the landed SFP-219 shape.
COMMAND_NAME = RequestUserInput.__name__

#: Reasons recorded on the move and non-moves (§8.5 / §8.8).
MOVE_REASON = "profile above LEVEL_1_INTERNAL: a user approval is required before merge (ID-024)"
INTERNAL_NO_MOVE_REASON = (
    "LEVEL_1_INTERNAL: no user approval required — the internal auto-merge path proceeds"
)
WRONG_STATE_REASON = (
    "not MERGING: the user-approval policy applies only while merging, before deploy"
)
NO_FACT_REASON = "no user-approval fact observed: the profile is not resolvable (fail-closed)"
UNKNOWN_PROFILE_REASON = (
    "validation profile not resolvable to a landed ValidationProfile: "
    "the workflow stays (fail-closed)"
)

#: The deterministic rendering of the profile fact string (see
#: :meth:`UserApprovalFact.to_fact_strings`).
_FACT_TEMPLATE = f"{USER_APPROVAL_FACT_KIND}:validation_profile:"


class UserApprovalPolicy:
    """Decide the user-approval edge from the PR-spec's validation profile.

    Pure and deterministic (AP-011): the same ``(state, facts)`` always yield
    an equal :class:`~orchestrator.domain.workflow.policy_engine.PolicyDecision`.
    No bus, no clock, no I/O; the ``RequestUserInput`` command rides along as
    a name only (§8.6) and is never built, dispatched, or executed here.

    The requirement is decided *solely* from the landed ``ValidationProfile``
    (ID-024/ID-067) via the contract's own
    :data:`~sfp_contracts.validation.profiles.REQUIRES_HUMAN_APPROVAL` set —
    never from an ad-hoc boolean, and never by re-listing the tiers here.
    """

    #: Convenience alias so callers can construct the fact without a second import.
    fact_type = UserApprovalFact

    def decide(
        self,
        current_state: WorkflowState,
        business_facts: Sequence[str],
    ) -> PolicyDecision:
        """Render this policy's verdict for the given state and facts.

        Routing (all non-moves are recorded §8.8 values, never exceptions):

        - ``MERGING`` + a tier that requires human approval
          (``LEVEL_2``/``LEVEL_3``/``LEVEL_4``) → transition to
          ``WAITING_FOR_USER``, referencing the ``RequestUserInput`` command
          name;
        - ``MERGING`` + ``LEVEL_1_INTERNAL`` → recorded no-move (the internal
          auto-merge path proceeds without a human);
        - any other state → no-transition (wrong stage);
        - no user-approval fact → no-transition, fail-closed;
        - a fact whose profile string matches no landed member →
          no-transition, fail-closed.
        """
        if current_state is not SOURCE_STATE:
            return PolicyDecision.no_transition_verdict(reason=WRONG_STATE_REASON)

        profile = self.parse_profile(business_facts)
        if profile is None:
            # Either no fact was observed at all, or one was observed whose
            # profile string is not a landed member. Both fail closed; the
            # absent-fact case is the common one and gets its own reason.
            if any(fact.startswith(_FACT_TEMPLATE) for fact in frozenset(business_facts)):
                return PolicyDecision.no_transition_verdict(reason=UNKNOWN_PROFILE_REASON)
            return PolicyDecision.no_transition_verdict(reason=NO_FACT_REASON)

        if profile not in REQUIRES_HUMAN_APPROVAL:
            return PolicyDecision.no_transition_verdict(reason=INTERNAL_NO_MOVE_REASON)

        return PolicyDecision.transition_verdict(
            TARGET_STATE,
            reason=MOVE_REASON,
            command_names=(COMMAND_NAME,),
        )

    @staticmethod
    def parse_profile(facts: Sequence[str]) -> ValidationProfile | None:
        """Lift the validation profile from the engine's fact strings — deterministic.

        Returns ``None`` when no landed profile can be lifted: either no
        user-approval fact is present, or the rendered profile string matches
        no :class:`ValidationProfile` member (the fail-closed input).
        Recognition is total and order-independent: exact deterministic
        renderings are matched, so unknown facts are ignored.
        """
        observed = frozenset(facts)
        for profile in ValidationProfile:
            if f"{_FACT_TEMPLATE}{profile.value}" in observed:
                return profile
        return None

    @staticmethod
    def parse_fact(facts: Sequence[str]) -> UserApprovalFact | None:
        """Lift the typed :class:`~.facts.UserApprovalFact`, or ``None`` if absent."""
        profile = UserApprovalPolicy.parse_profile(facts)
        return None if profile is None else UserApprovalFact(validation_profile=profile)


__all__ = [
    "COMMAND_NAME",
    "POLICY_NAME",
    "SOURCE_STATE",
    "TARGET_STATE",
    "UserApprovalPolicy",
]
