"""The CONFIRM state machine (SFP-135, ID-069 as amended 2026-09-15, MAS §9.4).

Consumes one inbound user reply to a summarized interaction and advances the
ID-069 confirmation gate. The gate is **multilingual and case-insensitive**
(the 2026-09-15 ID-069 amendment, SFP-258): a reply confirms when

1. its whole text is a word on :data:`CONFIRMATION_WORDS` (the curated
   deterministic fast path — EN/ES/CA/FR/DE, case-insensitive, surrounding
   punctuation/emoji trimmed), OR
2. the injected ``confirmation_intent_classifier`` hook says so (the general
   path — an optional callable, so hosts can plug any intent classifier), OR
3. it was treated as an adjust input, the summarize run classified it as a
   confirmation intent via its ``is_confirmation_intent`` structured output
   field, and the freshly regenerated summary is then confirmed (the LLM is
   a FALLBACK, never the only path — the curated list always decides first).

Any other reply is a correction that regenerates the summary (through the
SAME injected :class:`~communication.application.communication_agent.\
CommunicationAgent` — never a second LLM path) and re-requests confirmation;
on confirmation the confirmed summary is published as ``UserInputReceived``
and the interaction is completed. :meth:`ConfirmFlow.confirm_interaction`
exposes the same terminal step without a typed reply — the composition's
Block Kit ✅ Confirm button click routes there and is EXACTLY equivalent to
typed confirmation (same publication, same completion).

Grounded in:
- ID-069 (amended 2026-09-15) — human input captured from Slack must not be
  trusted as-is: the summary must be explicitly confirmed before it becomes
  the durable state the interaction carries. The gate applies to ALL
  interactions; confirmation is a button click OR a typed confirmation word
  in any language; ALL other pre-confirmation text is adjustment. The
  confirm-before-persist ordering is unchanged.
- MAS §9.4 (Summary / Closed Interactions) — the regenerated summary is
  persisted through the InteractionService (AP-009: durable summary, never
  a transcript); a COMPLETED or EXPIRED interaction is closed: the flow
  returns the SFP-134 :class:`~communication.application.communication_agent.\
ClosedInteractionOutcome` with ZERO mutations (no regenerate, no publish,
  no complete).
- ID-072 / MAS §6.3 (ownership) — the flow persists NO ``UserDecision``:
  decision persistence is the Orchestrator's. Communication's publication
  contract is exactly one ``UserInputReceived`` carrying the confirmed
  summary text on the confirm path; downstream consumption is not this
  flow's concern. (Guarded by the module-import test in the test suite.)
- PENDING_CONFIRM is CONVERSATION state, carried in the flow/interaction
  fields — NOT a new :class:`~communication.application.interaction_service.\
InteractionStatus` value. The enum stays ``ACTIVE`` / ``COMPLETED`` /
  ``EXPIRED``: an ACTIVE interaction with a summary awaiting the user's
  reply *is* the pending-confirmation state; there is no column, no
  migration, no enum member (MAS §9.4).
- MAS §12.7 (determinism) — no wall-clock read anywhere in this module:
  state derivation uses the injected clock; message timestamps stay with
  the caller.
- SFP-124 — envelope discipline: the confirm path never invents identity;
  the ``UserInputReceived`` envelope comes from the injectable factory,
  defaulting to the reference
  :func:`~communication.interfaces.slack_inbound.make_user_input_received_envelope`.

State read: SFP-129's :class:`~communication.application.interaction_service.\
InteractionService` exposes transitions and the derived ``status()`` read but
no full-state getter (its SFP-112 row carries the summary the gate confirms).
The flow therefore snapshots the interaction over the SAME injected
unit-of-work seam the service and the inbound consumer use
(:func:`~communication.application.interaction_service.session_factory`
discipline, deterministic newest-first lookup, derived status via
:func:`~communication.application.interaction_service.derive_status`) and
keeps every WRITE on the injected service — it never mutates a row itself.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, ClassVar, Final

from pydantic import BaseModel, ConfigDict, Field
from sfp_contracts.events import UserInputReceived
from sfp_contracts.events.envelope import EventEnvelope
from sqlalchemy import select

from communication.application.communication_agent import (
    ClosedInteractionOutcome,
    CommunicationAgent,
    InteractionState,
    InteractionSummary,
)
from communication.application.interaction_service import (
    Clock,
    InteractionService,
    InteractionStatus,
    SessionFactory,
    derive_status,
)
from communication.infrastructure.persistence import UserInteraction
from communication.interfaces.slack_inbound import (
    UserInputReceivedEnvelopeFactory,
    make_user_input_received_envelope,
)

if TYPE_CHECKING:
    # Annotation-only: importing this at runtime closes the
    # slack_inbound → interaction_service → application → confirm_flow →
    # slack_inbound import cycle (surfaces only on some import orders).
    from communication.interfaces.slack_inbound import SlackProviderMessage

if TYPE_CHECKING:
    from sfp_messaging.bus import MessageBus

__all__ = [
    "CONFIRM_LITERAL",
    "CONFIRMATION_WORDS",
    "ConfirmFlow",
    "ConfirmFlowResult",
    "ConfirmOutcome",
    "CorrectionOutcome",
    "is_curated_confirmation",
]

#: The exact confirmation literal (ID-069 as originally accepted). Retained
#: for reference and tests; the gate itself now matches the curated
#: multilingual word list below (the 2026-09-15 ID-069 amendment — see
#: docs/IMPLEMENTATION_DECISIONS.md).
CONFIRM_LITERAL: Final[str] = "CONFIRM"

#: The curated multilingual confirmation words (the ID-069 amendment,
#: SFP-258): the DETERMINISTIC fast path of the confirmation gate. A reply
#: whose whole text — stripped, case-folded, surrounding punctuation/emoji
#: trimmed — is one of these words confirms, in any of the covered
#: languages (EN / ES / CA / FR / DE), case-insensitively. Data, not logic:
#: extending coverage is an append to this frozenset. Anything NOT on the
#: list goes to the general path (the injected classifier hook, then the
#: summarize run's ``is_confirmation_intent`` structured output field) and
#: NEVER confirms on a heuristic.
CONFIRMATION_WORDS: Final[frozenset[str]] = frozenset(
    {
        # English
        "confirm",
        "confirmed",
        "confirming",
        "yes",
        "ok",
        "okay",
        # Spanish
        "confirmar",
        "confirmo",
        "confirmado",
        "si",
        "sí",
        "vale",
        "correcto",
        # Catalan
        "confirmi",
        "confirmat",
        "d'acord",
        "dacord",
        "correcte",
        # French
        "confirmer",
        "confirme",
        "confirmé",
        "confirmee",
        "confirmez",
        "oui",
        "d'accord",
        # German
        "bestätigen",
        "bestätigt",
        "bestaetigen",
        "bestaetigt",
        "ja",
        "in ordnung",
    }
)

#: Characters trimmed from BOTH ends before matching — surrounding
#: punctuation, quote marks and the emoji Slack commonly carries. Interior
#: text is untouched (``confirm now`` is NOT a curated confirmation).
_TRIM_CHARS: Final[str] = " \t\n\r.,;:!?¡¿…\"'`“”‘’()[]{}*~-_✅☑️👍✋"


def is_curated_confirmation(text: str) -> bool:
    """Deterministic fast path: is ``text`` a curated confirmation word?

    The whole message (never a substring) is stripped of surrounding
    whitespace/punctuation/emoji, case-folded, and compared against
    :data:`CONFIRMATION_WORDS`. Pure function — no I/O, no model call, no
    clock (MAS §12.7).
    """
    return text.strip().casefold().strip(_TRIM_CHARS).strip() in CONFIRMATION_WORDS


#: The terminal statuses that make an interaction CLOSED (MAS §9.4 / AP-005):
#: a reply on a closed interaction never mutates it.
_CLOSED_STATUSES: Final[frozenset[InteractionStatus]] = frozenset(
    {InteractionStatus.COMPLETED, InteractionStatus.EXPIRED}
)


class ConfirmOutcome(BaseModel):
    """The interaction was confirmed and completed (ID-069 terminal step).

    The reply was the exact literal ``CONFIRM``: the confirmed summary was
    published as ``UserInputReceived`` and the interaction was completed via
    the :class:`InteractionService`. Carries the fact for the caller (the
    wiring decides any user-facing acknowledgement).

    Frozen: an outcome is a fact about a completed confirmation, not work
    state.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    interaction_id: str = Field(min_length=1)
    confirmed_summary: str = Field(min_length=1)


class CorrectionOutcome(BaseModel):
    """The reply was a correction — the summary was regenerated (ID-069).

    Any non-``CONFIRM`` reply. The regenerated summary was persisted via the
    agent's
    :class:`~communication.application.communication_agent.\
InteractionSummaryWriter` port (the SAME injected
    :class:`InteractionService`), and the interaction stays open awaiting a
    re-confirmation — the caller re-requests confirmation by sending the
    regenerated summary back to the thread.

    Frozen: an outcome is a fact about one correction round, not work state.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    interaction_id: str = Field(min_length=1)
    regenerated_summary: str = Field(min_length=1)


#: What one handled reply yields. The closed-interaction branch delegates to
#: the SFP-134 outcome unchanged — the flow never re-wraps it.
ConfirmFlowResult = ConfirmOutcome | CorrectionOutcome | ClosedInteractionOutcome


class ConfirmFlow:
    """Advance the ID-069 CONFIRM gate for one inbound reply (SFP-135).

    Constructor-injected seams (all Protocol/service-typed; no concrete
    runtime, no Slack I/O):

    - ``bus`` — the vendor-neutral :class:`~sfp_messaging.bus.MessageBus`
      the confirm-path ``UserInputReceived`` is published onto.
    - ``interactions`` — the :class:`InteractionService` (the ONLY write
      path: ``complete``; the agent's own ``update_summary`` delegation
      lands on the same instance).
    - ``agent`` — the :class:`CommunicationAgent`; ``summarize`` is the
      ONLY regeneration path (never a second LLM call site).
    - ``session_factory`` — one unit of work for the state snapshot
      (:data:`~communication.application.interaction_service.SessionFactory`).
    - ``clock`` — the ONLY time seam (MAS §12.7), REQUIRED and injected: it
      is used solely to derive the interaction status when snapshotting.
      There is no wall-clock default and no ``datetime.now`` anywhere in
      this module — the same inputs always yield the same outcome.
    - ``input_envelope_factory`` — the confirm-path envelope seam (SFP-124
      discipline), defaulting to
      :func:`~communication.interfaces.slack_inbound.make_user_input_received_envelope`.
    - ``confirmation_intent_classifier`` — the general-path intent hook
      (the 2026-09-15 ID-069 amendment): an optional pure callable invoked
      ONLY when the curated word list misses. ``None`` (the default) means
      the hook is absent and the flow falls through to the adjust branch.
      The LLM fallback proper is the summarize run's
      ``is_confirmation_intent`` structured output field, consumed in
      :meth:`_correct` — never a second model call site.
    """

    def __init__(
        self,
        *,
        bus: MessageBus,
        interactions: InteractionService,
        agent: CommunicationAgent,
        session_factory: SessionFactory,
        clock: Clock,
        input_envelope_factory: UserInputReceivedEnvelopeFactory | None = None,
        confirmation_intent_classifier: Callable[[str], bool] | None = None,
    ) -> None:
        self._bus = bus
        self._interactions = interactions
        self._agent = agent
        self._session_factory = session_factory
        self._clock = clock
        self._input_envelope_factory = input_envelope_factory or make_user_input_received_envelope
        self._confirmation_intent_classifier = confirmation_intent_classifier

    async def handle(self, message: SlackProviderMessage) -> ConfirmFlowResult:
        """Route one reply through the CONFIRM state machine (ID-069).

        Order of evaluation — each branch is exclusive:

        1. **Closed check** — a COMPLETED or EXPIRED interaction returns the
           SFP-134 :class:`ClosedInteractionOutcome` BEFORE anything else:
           zero mutations, zero publishes, zero agent runs.
        2. **Confirmation gate** — the curated multilingual word list (fast
           path) or the injected classifier hook (general path) decides the
           reply is a confirmation: the ``UserInputReceived`` carrying the
           confirmed summary text is published, then the interaction is
           completed via the service.
        3. **Correction** — any other reply regenerates the summary through
           the injected agent (which persists it via its
           ``InteractionSummaryWriter`` port) and returns a
           :class:`CorrectionOutcome` requesting re-confirmation — UNLESS
           the run's ``is_confirmation_intent`` structured field says the
           message was actually a confirmation, in which case the freshly
           regenerated summary is confirmed (the LLM fallback path).

        Args:
            message: The typed inbound reply; ``provider_reference`` keys
                the interaction, ``text`` is matched against the curated
                word list / classifier.

        Returns:
            The typed outcome for the branch taken.

        Raises:
            LookupError: No interaction exists for the message's provider
                reference (fail-closed — a reply to an unknown thread is
                never silently dropped).
        """
        provider_reference = message.provider_reference
        state = self._snapshot(provider_reference)

        if state.status in _CLOSED_STATUSES:
            return ClosedInteractionOutcome(
                interaction_id=str(state.interaction_id),
                status=state.status,
                prior_summary=state.summary,
            )

        if self._is_confirmation(message.text):
            return await self._confirm(state, state.summary)
        return await self._correct(state, message)

    async def confirm_interaction(
        self, provider_reference: str
    ) -> ConfirmOutcome | ClosedInteractionOutcome:
        """Confirm the interaction WITHOUT a typed reply (SFP-258 button path).

        The Block Kit ✅ Confirm click routes here and is EXACTLY equivalent
        to a typed confirmation: same snapshot, same closed check, same
        ``UserInputReceived`` publication shape (``session_id`` =
        provider reference, ``text`` = the confirmed summary), same
        completion. Raises :class:`LookupError` for an unknown thread.

        Returns:
            :class:`ConfirmOutcome` on the open path;
            :class:`ClosedInteractionOutcome` (zero mutations, zero
            publishes) for a COMPLETED/EXPIRED interaction — the late-click
            case.
        """
        state = self._snapshot(provider_reference)
        if state.status in _CLOSED_STATUSES:
            return ClosedInteractionOutcome(
                interaction_id=str(state.interaction_id),
                status=state.status,
                prior_summary=state.summary,
            )
        return await self._confirm(state, state.summary)

    def _is_confirmation(self, text: str) -> bool:
        """The confirmation gate: curated fast path, then the hook."""
        if is_curated_confirmation(text):
            return True
        classifier = self._confirmation_intent_classifier
        return classifier is not None and classifier(text)

    async def _confirm(
        self,
        state: InteractionState,
        confirmed_summary: str,
    ) -> ConfirmOutcome:
        """Publish the confirmed summary and complete the interaction."""
        envelope: EventEnvelope = self._input_envelope_factory(
            UserInputReceived(session_id=state.provider_reference, text=confirmed_summary)
        )
        await self._bus.publish(envelope)
        await self._interactions.complete(state.provider_reference)
        return ConfirmOutcome(
            interaction_id=str(state.interaction_id),
            confirmed_summary=confirmed_summary,
        )

    async def _correct(
        self,
        state: InteractionState,
        message: SlackProviderMessage,
    ) -> ConfirmFlowResult:
        """Regenerate the summary from the correction via the SAME agent.

        The LLM confirmation-intent fallback (the 2026-09-15 ID-069
        amendment) lives here: when the run's ``is_confirmation_intent``
        structured field is true — i.e. the curated list and the hook both
        missed but the model classifies the message as a confirmation — the
        freshly regenerated (already persisted) summary is CONFIRMED instead
        of requesting re-confirmation. Everything else stays a correction.
        """
        summary = await self._agent.summarize(state, message)
        if isinstance(summary, ClosedInteractionOutcome):  # pragma: no cover —
            # unreachable: the closed check above already returned; kept as a
            # typed pass-through so the union stays honest.
            return summary
        if isinstance(summary, InteractionSummary) and summary.confirmation_intent:
            return await self._confirm(state, summary.summary)
        return CorrectionOutcome(
            interaction_id=str(state.interaction_id),
            regenerated_summary=summary.summary,
        )

    def _snapshot(self, provider_reference: str) -> InteractionState:
        """Read the ORM-free interaction state the gate decides on.

        One read-only unit of work over the injected session factory, the
        deterministic newest-first lookup (mirroring the service's own
        ``_load``), the status DERIVED via :func:`derive_status` against the
        injected clock — never a stored status (there is no column).

        Raises:
            LookupError: No interaction exists for the reference.
        """
        now = self._clock()
        with self._session_factory() as session:
            interaction = session.scalars(
                select(UserInteraction)
                .where(UserInteraction.provider_reference == provider_reference)
                .order_by(UserInteraction.created_at.desc())
                .limit(1)
            ).first()
            if interaction is None:
                raise LookupError(
                    f"No UserInteraction found for provider_reference={provider_reference!r}"
                )
            return InteractionState(
                provider_reference=interaction.provider_reference,
                interaction_id=interaction.interaction_id,
                summary=interaction.summary,
                last_message_emissor=interaction.last_message_emissor,
                last_message_timestamp=interaction.last_message_timestamp,
                status=derive_status(interaction, now=now),
            )
