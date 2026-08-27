"""The should-fail policy (MAS §8.14 / §8.8, ID-068/ID-069, SFP-144).

Decides whether an observed, *already classified* failure should move the
workflow to ``FAILED`` — from the :class:`~.facts.FailureFact` business facts:
one landed :class:`~sfp_contracts.workflow.failure.FailureClassification`
(the ``classify_failure`` output shape, SFP-75). The decision table (from the
PRSpec) over the ID-068 taxonomy:

=====  =========================  ===========================  ===============================
case   classification             decision                     reason
=====  =========================  ===========================  ===============================
(a)    DEVELOPMENT_FAILURE        recorded no-move             the Coder fixes and re-submits;
                                  (rework loop)                 never FAILED, never an escalation
(b)    BLOCKED, auto-recoverable  recorded no-move             retried once the condition clears
                                                               (ID-069); no human needed
(c)    BLOCKED, human-            recorded no-move,            the specific ``BlockedCause`` is
       recoverable                WAITING_FOR_USER             named and the ``RequestUserInput``
                                  semantics, the               command is referenced; the policy
                                  RequestUserInput command     only decides — driving
                                  referenced                   WAITING_FOR_USER is not this
                                                               policy's move
(d)    terminal genuine failure   move to FAILED               the factory cannot continue; the
                                  (NotifyUser referenced)      workflow ends as failed (§8.8)
=====  =========================  ===========================  ===============================

**Case (d) is surfaced, not implemented** (MAS §12.9 executability; the
PRSpec's own risk note anticipates exactly this). The landed taxonomy is
*total* over (a)–(c): :class:`~sfp_contracts.workflow.failure.FailureCategory`
has exactly two members, and ``classify_failure`` (SFP-75) partitions every
one of the eight :class:`~sfp_contracts.workflow.failure.BlockedCause` values
by the ``recoverable`` flag — four auto-recoverable (case b), four
human-recoverable (case c). No landed ``FailureClassification`` expresses a
"terminal genuine failure", and the PRSpec's ``out_of_scope`` forbids
inventing a new failure cause. Reaching for ``FAILED`` would therefore
require either a fabricated input vocabulary or a modification of the landed
contract — both out of bounds for the Coder. The ``FAILED`` edge itself
remains table-legal from the active states (SFP-137, MAS §8.8); what is
missing is the *contract-level marker* that names which classifications are
terminal. That is an upstream decision, recorded here and in this module's
``known_limitations`` rather than improvised.

Grounded in:
- MAS §8.14 — deterministic, side-effect-free policy functions of (state,
  observed facts) deciding the transition and the commands.
- MAS §8.8 — failures are business facts producing observable transitions;
  "no transition" is an observable, recorded outcome, never an exception and
  never a silent skip.
- MAS §8.6 — commands never modify workflow state; the policy only *carries*
  the command name as data.
- ID-068 — normal rework and development failures are handled by
  Coder/Reviewer without escalating; a ticket is BLOCKED only when the
  factory cannot continue without external intervention.
- ID-069 — auto-recoverable issues are retried; human-recoverable issues go
  through the CONFIRM flow.
- MAS §12.9 — facts and vocabulary come from what is landed; an unresolvable
  input is surfaced, never invented.
- ID-013 / AP-011 — plain-string serialization; purity.

Implements the SFP-142
:class:`~orchestrator.domain.workflow.policy_engine.WorkflowPolicy` protocol;
consumed through
:func:`~orchestrator.domain.workflow.policy_engine.evaluate`, which validates
the decided target against the SFP-137 table. This module never applies a
transition itself.
"""

from __future__ import annotations

from collections.abc import Sequence

from sfp_contracts.commands import NotifyUser, RequestUserInput
from sfp_contracts.workflow.failure import (
    BlockedCause,
    FailureCategory,
    FailureClassification,
)

from orchestrator.domain.workflow.policies.facts import (
    FAILURE_FACT_KIND,
    NONE,
    FailureFact,
)
from orchestrator.domain.workflow.policy_engine import PolicyDecision
from orchestrator.domain.workflow.states import ACTIVE_STATES, WorkflowState

#: The policy's recorded identifier (MAS §8.5 ``applied_policy``).
POLICY_NAME = "should-fail"

#: The states this policy owns the ``FAILED`` edge from (MAS §8.8): the
#: canonical ``ACTIVE_STATES`` expression in :mod:`.states` — read from the
#: landed module, never re-listed here, so the two can never drift.
SOURCE_STATES = ACTIVE_STATES

#: The target this policy owns for the surfaced case (d); legality itself
#: lives in the SFP-137 ``TRANSITIONS`` table and is never re-implemented
#: here. See the module docstring: no landed classification reaches it yet.
TARGET_STATE = WorkflowState.FAILED

#: The landed command case (d) would reference on the terminal move (as a
#: name only, §8.6). Declared for provenance and for the future landed
#: marker; deliberately unreferenced by :meth:`decide` until that marker
#: exists (see the module docstring).
COMMAND_NAME = NotifyUser.__name__

#: The landed escalation command the human-recoverable case references (as a
#: name only, §8.6) — the catalogue's ask-the-user command.
ESCALATION_COMMAND_NAME = RequestUserInput.__name__

#: Reasons recorded on the non-moves (§8.5 / §8.8). The human-recoverable
#: reason names the *specific* ``BlockedCause``.
DEVELOPMENT_FAILURE_REASON = (
    "development failure: the Coder fixes and re-submits (ID-068 rework loop); not FAILED"
)
BLOCKED_AUTO_RECOVERABLE_TEMPLATE = (
    "blocked ({cause}), auto-recoverable: retried once the condition clears (ID-069); not FAILED"
)
BLOCKED_HUMAN_RECOVERABLE_TEMPLATE = (
    "blocked ({cause}), human-recoverable: the user must decide (RequestUserInput); not FAILED"
)
WRONG_STATE_REASON = (
    "state not in the FAILED-sources set: the should-fail policy applies only to active states"
)
NO_FACT_REASON = "no failure fact observed: the workflow stays at this stage"
MALFORMED_FACT_REASON = (
    "failure fact present but not resolvable to a landed FailureClassification: "
    "the workflow stays (fail-closed)"
)

#: The deterministic renderings of the failure fact strings (see
#: :meth:`FailureFact.to_fact_strings`).
_FACT_PREFIX = f"{FAILURE_FACT_KIND}:"
_CATEGORY_FIELD = f"{_FACT_PREFIX}category:"
_CAUSE_FIELD = f"{_FACT_PREFIX}cause:"
_RECOVERABLE_FIELD = f"{_FACT_PREFIX}recoverable:"


def _render_cause(cause: BlockedCause | None) -> str:
    """Render a blocked cause — ``NONE`` when the classification has none."""
    return cause.value if cause is not None else NONE


class ShouldFailPolicy:
    """Decide the ``FAILED`` edge from a landed failure classification.

    Pure and deterministic (AP-011): the same ``(state, facts)`` always yield
    an equal :class:`~orchestrator.domain.workflow.policy_engine.PolicyDecision`.
    No bus, no clock, no I/O; command names ride along as names only (§8.6)
    and are never built, dispatched, or executed here.

    The taxonomy is consumed, never re-enumerated: categories and causes come
    from the landed :mod:`sfp_contracts.workflow.failure` members. Cases
    (a)–(c) are exhaustive over those members, so every landed
    classification yields a recorded no-move (see the module docstring for
    the surfaced case (d)).
    """

    #: Convenience alias so callers can construct the fact without a second import.
    fact_type = FailureFact

    def decide(
        self,
        current_state: WorkflowState,
        business_facts: Sequence[str],
    ) -> PolicyDecision:
        """Render this policy's verdict for the given state and facts.

        Routing (all non-moves are recorded §8.8 values, never exceptions):

        - any state outside the FAILED-sources set → no-transition (wrong
          stage), regardless of the classification;
        - (a) ``DEVELOPMENT_FAILURE`` → no-move: the ID-068 rework loop owns
          it — no FAILED target and no escalation command, ever;
        - (b) ``BLOCKED`` + ``recoverable`` → no-move: it is retried;
        - (c) ``BLOCKED`` + not recoverable → no-move naming the specific
          ``BlockedCause``, referencing ``RequestUserInput`` (decide only —
          driving ``WAITING_FOR_USER`` is not this policy's move);
        - no failure fact → no-transition (absent fact);
        - a failure fact that does not resolve to a landed classification →
          no-transition (fail-closed).
        """
        if current_state not in SOURCE_STATES:
            return PolicyDecision.no_transition_verdict(reason=WRONG_STATE_REASON)

        observed = frozenset(business_facts)
        if not any(fact.startswith(_FACT_PREFIX) for fact in observed):
            return PolicyDecision.no_transition_verdict(reason=NO_FACT_REASON)

        classification = self.parse_classification(observed)
        if classification is None:
            return PolicyDecision.no_transition_verdict(reason=MALFORMED_FACT_REASON)

        if classification.category is FailureCategory.DEVELOPMENT_FAILURE:
            # (a) ID-068: the implementation loop owns it. No FAILED target
            # and no escalation command can be requested on this path.
            return PolicyDecision.no_transition_verdict(reason=DEVELOPMENT_FAILURE_REASON)

        if classification.recoverable:
            # (b) ID-069: retried once the condition clears. No human.
            return PolicyDecision.no_transition_verdict(
                reason=BLOCKED_AUTO_RECOVERABLE_TEMPLATE.format(
                    cause=_render_cause(classification.cause)
                )
            )

        # (c) ID-069 CONFIRM flow: the specific cause is named and the
        # ask-the-user command is referenced *alongside* the non-move. The
        # policy does not drive WAITING_FOR_USER.
        return PolicyDecision(
            target_state=None,
            no_transition=True,
            reason=BLOCKED_HUMAN_RECOVERABLE_TEMPLATE.format(
                cause=_render_cause(classification.cause)
            ),
            command_names=(ESCALATION_COMMAND_NAME,),
        )

    @staticmethod
    def parse_classification(facts: frozenset[str]) -> FailureClassification | None:
        """Lift the classification from the engine's fact strings — deterministic.

        Returns ``None`` when the observed failure-fact strings do not resolve
        to a landed :class:`FailureClassification` (absent routing fields,
        values matching no landed member, or a pairing no landed
        classification produces). Recognition is total and order-independent:
        the exact deterministic renderings are matched, so unknown facts are
        ignored.

        The shape guard mirrors the landed contract's own field rule: a
        ``BLOCKED`` classification *always* carries a ``BlockedCause``, and
        only ``DEVELOPMENT_FAILURE`` carries ``cause=None``. A fact set that
        violates that pairing (e.g. ``BLOCKED`` with no cause) is not the
        rendering of any landed classification, so it fails closed rather
        than being routed — this keeps
        :meth:`parse_fact` ∘ :meth:`FailureFact.to_fact_strings` the identity
        over landed classifications.
        """
        category: FailureCategory | None = None
        for member in FailureCategory:
            if f"{_CATEGORY_FIELD}{member.value}" in facts:
                category = member
                break
        if category is None:
            return None

        cause: BlockedCause | None = None
        for blocked_member in BlockedCause:
            if f"{_CAUSE_FIELD}{blocked_member.value}" in facts:
                cause = blocked_member
                break

        recoverable: bool | None = None
        if f"{_RECOVERABLE_FIELD}True" in facts:
            recoverable = True
        elif f"{_RECOVERABLE_FIELD}False" in facts:
            recoverable = False
        if recoverable is None:
            return None

        if category is FailureCategory.BLOCKED and cause is None:
            return None
        if category is FailureCategory.DEVELOPMENT_FAILURE and cause is not None:
            return None

        return FailureClassification(
            category=category,
            cause=cause,
            recoverable=recoverable,
        )

    @staticmethod
    def parse_fact(facts: Sequence[str]) -> FailureFact | None:
        """Lift the typed :class:`~.facts.FailureFact`, or ``None`` if absent.

        Distinct from :meth:`parse_classification`: this reports whether a
        *failure fact* is present at all, mirroring the sibling policies'
        ``parse_fact`` shape.
        """
        observed = frozenset(facts)
        if not any(fact.startswith(_FACT_PREFIX) for fact in observed):
            return None
        classification = ShouldFailPolicy.parse_classification(observed)
        return None if classification is None else FailureFact(classification=classification)


__all__ = [
    "COMMAND_NAME",
    "ESCALATION_COMMAND_NAME",
    "POLICY_NAME",
    "SOURCE_STATES",
    "TARGET_STATE",
    "ShouldFailPolicy",
]
