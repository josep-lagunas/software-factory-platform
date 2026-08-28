"""The application-layer command emitters (MAS §5.3 / §4.7, SFP-152 / SFP-245).

Grounded in:
- MAS §5.3 — the command catalogue: commands are how the Orchestrator moves
  work *to* other agents; they carry intent, never state mutation.
- MAS §4.7 — every command rides a :class:`~sfp_contracts.messages.MessageEnvelope`
  of routing/dedup metadata plus a per-command payload.
- MAS §8.6 — the workflow advances only on events / user decisions; a command
  emitter never consults the workflow table and never transitions state.
- MAS §11.8 — communication commands (``REQUEST_USER_INPUT`` / ``NOTIFY_USER``)
  fire immediately and are never admission-gated: they are the user-facing
  surface, not scheduled work competing for scheduler slots.
- ID-031 — the authoritative envelope field set and command names.
- ID-072 — the Orchestrator *decides* and issues commands; the Workspace
  Worker *executes* them. Emitting is the decision's output, not the work
  itself; envelopes are addressed per ID-072 issuer ownership.
- SFP-219 — :class:`~sfp_contracts.commands.envelope.CommandEnvelope` carries
  the ``command_type`` discriminator plus a typed payload; there is no
  envelope-level consistency validator.
- SFP-42 — the :class:`~sfp_messaging.bus.MessageBus` protocol is the only
  I/O seam this module touches.
- SFP-137 — the structural template: :class:`WorkflowTransitionPublisher`
  fixed the shape (constructor-injected bus, an optional envelope factory
  seam, validate/decide first then publish). This module mirrors it on the
  command side.
- SFP-152 — :class:`ExecuteCodingJobEmitter` landed first and fixed the
  per-emitter shape the seven siblings below mirror exactly.
- SFP-45 (NOT landed) — the serde-layer envelope consistency check does not
  exist yet and is NOT relied upon: every emitter runs the shared local
  payload/discriminator consistency check *before* any bus call.

Shape (per the PRSpec implementation notes):

- :class:`ExecuteCodingJobEmitter` is the first emitter (SFP-152). The seven
  sibling emitters below (SFP-245) complete the catalogue as additive classes
  in this same module, each with its own payload/discriminator pair and no
  shared mutable state.
- ``emit(...)`` is a thin async method: build/accept the payload, run the
  local consistency check, construct the envelope, publish, return it.
- The payload/discriminator consistency check is ONE shared private helper,
  :func:`_check_payload_matches_discriminator` — the SFP-152 implementation
  was refactored onto it, so all eight emitters call it with zero copy-paste.
- Purity discipline: no clock (``occurred_at`` is caller-supplied), no
  randomness, no I/O beyond the injected bus, no workflow-table access, no
  state transition. The emitter never invents or mutates envelope identity —
  ``idempotency_key`` is carried verbatim because dedup is the bus/consumer's
  job, not the emitter's.

Fire conditions (documented per emitter; an emitter only publishes — it never
inspects workflow state to decide *whether* to fire, MAS §8.6):

- ``REVIEW_PULL_REQUEST`` — the review stage: ``CODING_IN_PROGRESS`` →
  ``REVIEW_IN_PROGRESS``, i.e. the PR-created fact.
- ``SYNCHRONIZE_PULL_REQUEST`` — the PR base has moved: origin/main advanced
  while the PR was open (SFP-240 semantics).
- ``REQUEST_MERGE`` — only after merge approval (the SFP-144
  ``UserApprovalPolicy`` decision: a LEVEL_1 auto-approval, or a human
  APPROVE out of ``WAITING_FOR_USER``).
- ``REQUEST_USER_INPUT`` / ``NOTIFY_USER`` — fire immediately, never
  admission-gated (MAS §11.8).
- ``CANCEL_CODING_JOB`` / ``CANCEL_REVIEW_JOB`` — genuine-failure
  classification (a ``ShouldFailPolicy`` terminal row) or owner cancel while
  the job is in flight.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import TypeVar

    from sfp_contracts.commands.envelope import CommandEnvelope, CommandType
    from sfp_contracts.commands.payloads import (
        CancelCodingJob,
        CancelReviewJob,
        CommandPayload,
        ExecuteCodingJob,
        NotifyUser,
        RequestMerge,
        RequestUserInput,
        ReviewPullRequest,
        SynchronizePullRequest,
    )
    from sfp_messaging.bus import MessageBus

    _PayloadT = TypeVar("_PayloadT", bound=CommandPayload)

    #: Seam: custom envelope construction, mirroring the
    #: ``WorkflowTransitionPublisher`` ``EnvelopeFactory`` seam (SFP-137).
    #: Generic over the payload each emitter accepts. The deterministic
    #: emitter never invents ``message_id`` / ``idempotency_key`` /
    #: ``correlation_id`` / ``causation_id`` / ``occurred_at`` — they are
    #: caller-supplied or supplied by this factory.
    CommandEnvelopeFactory = Callable[[_PayloadT], CommandEnvelope]


def _check_payload_matches_discriminator(
    payload: object,
    expected: type[CommandPayload],
    command_type: CommandType,
) -> None:
    """Assert the payload type matches the declared command discriminator.

    SFP-45 (the serde-layer envelope consistency check) is not landed and is
    not relied upon: this module-local check is the guard. It is the ONE
    shared private helper every emitter calls (SFP-245 refactored the SFP-152
    implementation onto it — eight callers, zero copy-paste). It raises
    :class:`ValueError` naming both the expected and the actual payload type,
    and it runs *before* any bus call so a mismatched command never reaches
    the wire.
    """
    if type(payload) is not expected:
        raise ValueError(
            f"{command_type.value} command payload must be {expected!r}, got {type(payload)!r}"
        )


class ExecuteCodingJobEmitter:
    """Publish the ``EXECUTE_CODING_JOB`` command on the injected bus (SFP-152).

    The first application-layer command emitter, mirroring
    :class:`~orchestrator.domain.workflow.state_machine.WorkflowTransitionPublisher`
    structurally (SFP-137): constructor-injected
    :class:`~sfp_messaging.bus.MessageBus`, an optional injected envelope
    factory seam, and a thin async method that validates before it publishes.

    Constructor-injected seams:

    - ``bus`` — the vendor-neutral ``MessageBus`` (in-memory today per the
      software-first owner decision; SFP-101 re-plumbs). ``emit`` publishes
      exactly one :class:`~sfp_contracts.commands.envelope.CommandEnvelope`
      per call.
    - ``envelope_factory`` — optional callable producing the
      ``CommandEnvelope``. ``message_id`` / ``idempotency_key`` /
      ``correlation_id`` / ``causation_id`` / ``occurred_at`` are runtime
      policy — the deterministic emitter never invents them. When omitted,
      the emitter builds the envelope from the caller-supplied identity
      fields verbatim.

    The emitter performs no state transition and never consults the workflow
    table (MAS §8.6): it touches only the bus seam.
    """

    def __init__(
        self,
        bus: MessageBus,
        *,
        envelope_factory: CommandEnvelopeFactory[ExecuteCodingJob] | None = None,
    ) -> None:
        self._bus = bus
        self._envelope_factory = envelope_factory

    async def emit(
        self,
        payload: ExecuteCodingJob,
        *,
        message_id: str,
        idempotency_key: str,
        correlation_id: str,
        causation_id: str,
        occurred_at: str,
    ) -> CommandEnvelope:
        """Construct and publish the ``EXECUTE_CODING_JOB`` command envelope.

        Flow: (1) accept the caller's ``ExecuteCodingJob`` payload; (2) run
        the shared consistency check — a mismatched payload type raises
        :class:`ValueError` naming the expected/actual pair *before* anything
        is constructed for or sent to the bus; (3) construct the
        ``CommandEnvelope`` with ``command_type=EXECUTE_CODING_JOB``, the
        payload, and the caller-supplied identity fields verbatim
        (``idempotency_key`` is never regenerated or mutated — dedup is the
        bus/consumer's job); (4) ``await bus.publish(envelope)``, letting
        failures propagate (no retry, no swallow); (5) return the envelope.

        Returns:
            The published :class:`~sfp_contracts.commands.envelope.CommandEnvelope`,
            for inspection/assertion by the caller.
        """
        from sfp_contracts.commands.envelope import CommandType
        from sfp_contracts.commands.payloads import ExecuteCodingJob

        _check_payload_matches_discriminator(
            payload, ExecuteCodingJob, CommandType.EXECUTE_CODING_JOB
        )
        envelope = self._build_envelope(
            payload,
            command_type=CommandType.EXECUTE_CODING_JOB,
            message_id=message_id,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
            causation_id=causation_id,
            occurred_at=occurred_at,
        )
        await self._bus.publish(envelope)
        return envelope

    def _build_envelope(
        self,
        payload: ExecuteCodingJob,
        *,
        command_type: CommandType,
        message_id: str,
        idempotency_key: str,
        correlation_id: str,
        causation_id: str,
        occurred_at: str,
    ) -> CommandEnvelope:
        """Build the ``CommandEnvelope`` (or delegate to the factory)."""
        if self._envelope_factory is not None:
            return self._envelope_factory(payload)
        from sfp_contracts.commands.envelope import CommandEnvelope

        return CommandEnvelope(
            message_id=message_id,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
            causation_id=causation_id,
            occurred_at=occurred_at,
            command_type=command_type,
            payload=payload,
        )


class ReviewPullRequestEmitter:
    """Publish the ``REVIEW_PULL_REQUEST`` command on the injected bus (SFP-245).

    An exact structural sibling of :class:`ExecuteCodingJobEmitter` (SFP-152):
    the same constructor seams and the same thin async ``emit`` contract.

    Fire condition: the review stage — ``CODING_IN_PROGRESS`` →
    ``REVIEW_IN_PROGRESS``, the PR-created fact. Coding produced an open PR,
    so the Reviewer is commanded to review it (MAS §5.3 / ID-072). The emitter
    never reads the workflow table to decide that (MAS §8.6): the caller
    emits when the PR-created fact holds.

    Constructor-injected seams (identical to the SFP-152 emitter):

    - ``bus`` — the vendor-neutral ``MessageBus``; ``emit`` publishes exactly
      one ``CommandEnvelope`` per call.
    - ``envelope_factory`` — optional callable producing the
      ``CommandEnvelope``; when omitted, the emitter builds it from the
      caller-supplied identity fields verbatim.
    """

    def __init__(
        self,
        bus: MessageBus,
        *,
        envelope_factory: CommandEnvelopeFactory[ReviewPullRequest] | None = None,
    ) -> None:
        self._bus = bus
        self._envelope_factory = envelope_factory

    async def emit(
        self,
        payload: ReviewPullRequest,
        *,
        message_id: str,
        idempotency_key: str,
        correlation_id: str,
        causation_id: str,
        occurred_at: str,
    ) -> CommandEnvelope:
        """Construct and publish the ``REVIEW_PULL_REQUEST`` command envelope.

        Flow: (1) accept the caller's ``ReviewPullRequest`` payload; (2) run
        the shared consistency check — a mismatched payload type raises
        :class:`ValueError` naming the expected/actual pair *before* anything
        is constructed for or sent to the bus; (3) construct the
        ``CommandEnvelope`` with ``command_type=REVIEW_PULL_REQUEST``, the
        payload, and the caller-supplied identity fields verbatim
        (``idempotency_key`` is never regenerated or mutated — dedup is the
        bus/consumer's job); (4) ``await bus.publish(envelope)``, letting
        failures propagate (no retry, no swallow); (5) return the envelope.

        Returns:
            The published :class:`~sfp_contracts.commands.envelope.CommandEnvelope`,
            for inspection/assertion by the caller.
        """
        from sfp_contracts.commands.envelope import CommandType
        from sfp_contracts.commands.payloads import ReviewPullRequest

        _check_payload_matches_discriminator(
            payload, ReviewPullRequest, CommandType.REVIEW_PULL_REQUEST
        )
        envelope = self._build_envelope(
            payload,
            command_type=CommandType.REVIEW_PULL_REQUEST,
            message_id=message_id,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
            causation_id=causation_id,
            occurred_at=occurred_at,
        )
        await self._bus.publish(envelope)
        return envelope

    def _build_envelope(
        self,
        payload: ReviewPullRequest,
        *,
        command_type: CommandType,
        message_id: str,
        idempotency_key: str,
        correlation_id: str,
        causation_id: str,
        occurred_at: str,
    ) -> CommandEnvelope:
        """Build the ``CommandEnvelope`` (or delegate to the factory)."""
        if self._envelope_factory is not None:
            return self._envelope_factory(payload)
        from sfp_contracts.commands.envelope import CommandEnvelope

        return CommandEnvelope(
            message_id=message_id,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
            causation_id=causation_id,
            occurred_at=occurred_at,
            command_type=command_type,
            payload=payload,
        )


class SynchronizePullRequestEmitter:
    """Publish the ``SYNCHRONIZE_PULL_REQUEST`` command on the injected bus.

    An exact structural sibling of :class:`ExecuteCodingJobEmitter` (SFP-152).

    Fire condition: the PR base has moved — origin/main advanced while the PR
    was open (SFP-240 semantics), so the Coder is commanded to push and
    synchronize the branch rather than have it reviewed on a stale base. The
    emitter never reads git or workflow state to decide that (MAS §8.6).

    Constructor-injected seams (identical to the SFP-152 emitter):

    - ``bus`` — the vendor-neutral ``MessageBus``; ``emit`` publishes exactly
      one ``CommandEnvelope`` per call.
    - ``envelope_factory`` — optional callable producing the
      ``CommandEnvelope``; when omitted, the emitter builds it from the
      caller-supplied identity fields verbatim.
    """

    def __init__(
        self,
        bus: MessageBus,
        *,
        envelope_factory: CommandEnvelopeFactory[SynchronizePullRequest] | None = None,
    ) -> None:
        self._bus = bus
        self._envelope_factory = envelope_factory

    async def emit(
        self,
        payload: SynchronizePullRequest,
        *,
        message_id: str,
        idempotency_key: str,
        correlation_id: str,
        causation_id: str,
        occurred_at: str,
    ) -> CommandEnvelope:
        """Construct and publish the ``SYNCHRONIZE_PULL_REQUEST`` envelope.

        Flow: (1) accept the caller's ``SynchronizePullRequest`` payload; (2)
        run the shared consistency check — a mismatched payload type raises
        :class:`ValueError` naming the expected/actual pair *before* anything
        is constructed for or sent to the bus; (3) construct the
        ``CommandEnvelope`` with ``command_type=SYNCHRONIZE_PULL_REQUEST``, the
        payload, and the caller-supplied identity fields verbatim
        (``idempotency_key`` is never regenerated or mutated — dedup is the
        bus/consumer's job); (4) ``await bus.publish(envelope)``, letting
        failures propagate (no retry, no swallow); (5) return the envelope.

        Returns:
            The published :class:`~sfp_contracts.commands.envelope.CommandEnvelope`,
            for inspection/assertion by the caller.
        """
        from sfp_contracts.commands.envelope import CommandType
        from sfp_contracts.commands.payloads import SynchronizePullRequest

        _check_payload_matches_discriminator(
            payload, SynchronizePullRequest, CommandType.SYNCHRONIZE_PULL_REQUEST
        )
        envelope = self._build_envelope(
            payload,
            command_type=CommandType.SYNCHRONIZE_PULL_REQUEST,
            message_id=message_id,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
            causation_id=causation_id,
            occurred_at=occurred_at,
        )
        await self._bus.publish(envelope)
        return envelope

    def _build_envelope(
        self,
        payload: SynchronizePullRequest,
        *,
        command_type: CommandType,
        message_id: str,
        idempotency_key: str,
        correlation_id: str,
        causation_id: str,
        occurred_at: str,
    ) -> CommandEnvelope:
        """Build the ``CommandEnvelope`` (or delegate to the factory)."""
        if self._envelope_factory is not None:
            return self._envelope_factory(payload)
        from sfp_contracts.commands.envelope import CommandEnvelope

        return CommandEnvelope(
            message_id=message_id,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
            causation_id=causation_id,
            occurred_at=occurred_at,
            command_type=command_type,
            payload=payload,
        )


class RequestMergeEmitter:
    """Publish the ``REQUEST_MERGE`` command on the injected bus (SFP-245).

    An exact structural sibling of :class:`ExecuteCodingJobEmitter` (SFP-152).

    Fire condition: ONLY after merge approval — the SFP-144
    ``UserApprovalPolicy`` decision (a LEVEL_1 auto-approval, or a human
    APPROVE out of ``WAITING_FOR_USER``). The merge *decision* is the
    Orchestrator's (ID-072); this emitter is that decision's output, not the
    decision itself. The emitter never reads approval state to decide that
    (MAS §8.6): the caller emits once approval holds.

    Constructor-injected seams (identical to the SFP-152 emitter):

    - ``bus`` — the vendor-neutral ``MessageBus``; ``emit`` publishes exactly
      one ``CommandEnvelope`` per call.
    - ``envelope_factory`` — optional callable producing the
      ``CommandEnvelope``; when omitted, the emitter builds it from the
      caller-supplied identity fields verbatim.
    """

    def __init__(
        self,
        bus: MessageBus,
        *,
        envelope_factory: CommandEnvelopeFactory[RequestMerge] | None = None,
    ) -> None:
        self._bus = bus
        self._envelope_factory = envelope_factory

    async def emit(
        self,
        payload: RequestMerge,
        *,
        message_id: str,
        idempotency_key: str,
        correlation_id: str,
        causation_id: str,
        occurred_at: str,
    ) -> CommandEnvelope:
        """Construct and publish the ``REQUEST_MERGE`` command envelope.

        Flow: (1) accept the caller's ``RequestMerge`` payload; (2) run the
        shared consistency check — a mismatched payload type raises
        :class:`ValueError` naming the expected/actual pair *before* anything
        is constructed for or sent to the bus; (3) construct the
        ``CommandEnvelope`` with ``command_type=REQUEST_MERGE``, the payload,
        and the caller-supplied identity fields verbatim
        (``idempotency_key`` is never regenerated or mutated — dedup is the
        bus/consumer's job); (4) ``await bus.publish(envelope)``, letting
        failures propagate (no retry, no swallow); (5) return the envelope.

        Returns:
            The published :class:`~sfp_contracts.commands.envelope.CommandEnvelope`,
            for inspection/assertion by the caller.
        """
        from sfp_contracts.commands.envelope import CommandType
        from sfp_contracts.commands.payloads import RequestMerge

        _check_payload_matches_discriminator(payload, RequestMerge, CommandType.REQUEST_MERGE)
        envelope = self._build_envelope(
            payload,
            command_type=CommandType.REQUEST_MERGE,
            message_id=message_id,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
            causation_id=causation_id,
            occurred_at=occurred_at,
        )
        await self._bus.publish(envelope)
        return envelope

    def _build_envelope(
        self,
        payload: RequestMerge,
        *,
        command_type: CommandType,
        message_id: str,
        idempotency_key: str,
        correlation_id: str,
        causation_id: str,
        occurred_at: str,
    ) -> CommandEnvelope:
        """Build the ``CommandEnvelope`` (or delegate to the factory)."""
        if self._envelope_factory is not None:
            return self._envelope_factory(payload)
        from sfp_contracts.commands.envelope import CommandEnvelope

        return CommandEnvelope(
            message_id=message_id,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
            causation_id=causation_id,
            occurred_at=occurred_at,
            command_type=command_type,
            payload=payload,
        )


class RequestUserInputEmitter:
    """Publish the ``REQUEST_USER_INPUT`` command on the injected bus (SFP-245).

    An exact structural sibling of :class:`ExecuteCodingJobEmitter` (SFP-152).

    Fire condition: immediately, never admission-gated (MAS §11.8) — asking a
    human a question is the user-facing surface, not scheduled work competing
    for a scheduler slot, so it is emitted the moment the Orchestrator needs
    input. The emitter performs no gating itself (MAS §8.6).

    Constructor-injected seams (identical to the SFP-152 emitter):

    - ``bus`` — the vendor-neutral ``MessageBus``; ``emit`` publishes exactly
      one ``CommandEnvelope`` per call.
    - ``envelope_factory`` — optional callable producing the
      ``CommandEnvelope``; when omitted, the emitter builds it from the
      caller-supplied identity fields verbatim.
    """

    def __init__(
        self,
        bus: MessageBus,
        *,
        envelope_factory: CommandEnvelopeFactory[RequestUserInput] | None = None,
    ) -> None:
        self._bus = bus
        self._envelope_factory = envelope_factory

    async def emit(
        self,
        payload: RequestUserInput,
        *,
        message_id: str,
        idempotency_key: str,
        correlation_id: str,
        causation_id: str,
        occurred_at: str,
    ) -> CommandEnvelope:
        """Construct and publish the ``REQUEST_USER_INPUT`` command envelope.

        Flow: (1) accept the caller's ``RequestUserInput`` payload; (2) run
        the shared consistency check — a mismatched payload type raises
        :class:`ValueError` naming the expected/actual pair *before* anything
        is constructed for or sent to the bus; (3) construct the
        ``CommandEnvelope`` with ``command_type=REQUEST_USER_INPUT``, the
        payload, and the caller-supplied identity fields verbatim
        (``idempotency_key`` is never regenerated or mutated — dedup is the
        bus/consumer's job); (4) ``await bus.publish(envelope)``, letting
        failures propagate (no retry, no swallow); (5) return the envelope.

        Returns:
            The published :class:`~sfp_contracts.commands.envelope.CommandEnvelope`,
            for inspection/assertion by the caller.
        """
        from sfp_contracts.commands.envelope import CommandType
        from sfp_contracts.commands.payloads import RequestUserInput

        _check_payload_matches_discriminator(
            payload, RequestUserInput, CommandType.REQUEST_USER_INPUT
        )
        envelope = self._build_envelope(
            payload,
            command_type=CommandType.REQUEST_USER_INPUT,
            message_id=message_id,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
            causation_id=causation_id,
            occurred_at=occurred_at,
        )
        await self._bus.publish(envelope)
        return envelope

    def _build_envelope(
        self,
        payload: RequestUserInput,
        *,
        command_type: CommandType,
        message_id: str,
        idempotency_key: str,
        correlation_id: str,
        causation_id: str,
        occurred_at: str,
    ) -> CommandEnvelope:
        """Build the ``CommandEnvelope`` (or delegate to the factory)."""
        if self._envelope_factory is not None:
            return self._envelope_factory(payload)
        from sfp_contracts.commands.envelope import CommandEnvelope

        return CommandEnvelope(
            message_id=message_id,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
            causation_id=causation_id,
            occurred_at=occurred_at,
            command_type=command_type,
            payload=payload,
        )


class NotifyUserEmitter:
    """Publish the ``NOTIFY_USER`` command on the injected bus (SFP-245).

    An exact structural sibling of :class:`ExecuteCodingJobEmitter` (SFP-152).

    Fire condition: immediately, never admission-gated (MAS §11.8) — telling
    a human something is the user-facing surface, not scheduled work competing
    for a scheduler slot. The emitter performs no gating itself (MAS §8.6).

    Constructor-injected seams (identical to the SFP-152 emitter):

    - ``bus`` — the vendor-neutral ``MessageBus``; ``emit`` publishes exactly
      one ``CommandEnvelope`` per call.
    - ``envelope_factory`` — optional callable producing the
      ``CommandEnvelope``; when omitted, the emitter builds it from the
      caller-supplied identity fields verbatim.
    """

    def __init__(
        self,
        bus: MessageBus,
        *,
        envelope_factory: CommandEnvelopeFactory[NotifyUser] | None = None,
    ) -> None:
        self._bus = bus
        self._envelope_factory = envelope_factory

    async def emit(
        self,
        payload: NotifyUser,
        *,
        message_id: str,
        idempotency_key: str,
        correlation_id: str,
        causation_id: str,
        occurred_at: str,
    ) -> CommandEnvelope:
        """Construct and publish the ``NOTIFY_USER`` command envelope.

        Flow: (1) accept the caller's ``NotifyUser`` payload; (2) run the
        shared consistency check — a mismatched payload type raises
        :class:`ValueError` naming the expected/actual pair *before* anything
        is constructed for or sent to the bus; (3) construct the
        ``CommandEnvelope`` with ``command_type=NOTIFY_USER``, the payload,
        and the caller-supplied identity fields verbatim
        (``idempotency_key`` is never regenerated or mutated — dedup is the
        bus/consumer's job); (4) ``await bus.publish(envelope)``, letting
        failures propagate (no retry, no swallow); (5) return the envelope.

        Returns:
            The published :class:`~sfp_contracts.commands.envelope.CommandEnvelope`,
            for inspection/assertion by the caller.
        """
        from sfp_contracts.commands.envelope import CommandType
        from sfp_contracts.commands.payloads import NotifyUser

        _check_payload_matches_discriminator(payload, NotifyUser, CommandType.NOTIFY_USER)
        envelope = self._build_envelope(
            payload,
            command_type=CommandType.NOTIFY_USER,
            message_id=message_id,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
            causation_id=causation_id,
            occurred_at=occurred_at,
        )
        await self._bus.publish(envelope)
        return envelope

    def _build_envelope(
        self,
        payload: NotifyUser,
        *,
        command_type: CommandType,
        message_id: str,
        idempotency_key: str,
        correlation_id: str,
        causation_id: str,
        occurred_at: str,
    ) -> CommandEnvelope:
        """Build the ``CommandEnvelope`` (or delegate to the factory)."""
        if self._envelope_factory is not None:
            return self._envelope_factory(payload)
        from sfp_contracts.commands.envelope import CommandEnvelope

        return CommandEnvelope(
            message_id=message_id,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
            causation_id=causation_id,
            occurred_at=occurred_at,
            command_type=command_type,
            payload=payload,
        )


class CancelCodingJobEmitter:
    """Publish the ``CANCEL_CODING_JOB`` command on the injected bus (SFP-245).

    An exact structural sibling of :class:`ExecuteCodingJobEmitter` (SFP-152).

    Fire condition: genuine-failure classification — a ``ShouldFailPolicy``
    terminal row — or an owner cancel while the coding job is in flight. The
    emitter never reads failure classification to decide that (MAS §8.6): the
    caller emits once the classification or the cancel holds.

    Constructor-injected seams (identical to the SFP-152 emitter):

    - ``bus`` — the vendor-neutral ``MessageBus``; ``emit`` publishes exactly
      one ``CommandEnvelope`` per call.
    - ``envelope_factory`` — optional callable producing the
      ``CommandEnvelope``; when omitted, the emitter builds it from the
      caller-supplied identity fields verbatim.
    """

    def __init__(
        self,
        bus: MessageBus,
        *,
        envelope_factory: CommandEnvelopeFactory[CancelCodingJob] | None = None,
    ) -> None:
        self._bus = bus
        self._envelope_factory = envelope_factory

    async def emit(
        self,
        payload: CancelCodingJob,
        *,
        message_id: str,
        idempotency_key: str,
        correlation_id: str,
        causation_id: str,
        occurred_at: str,
    ) -> CommandEnvelope:
        """Construct and publish the ``CANCEL_CODING_JOB`` command envelope.

        Flow: (1) accept the caller's ``CancelCodingJob`` payload; (2) run
        the shared consistency check — a mismatched payload type raises
        :class:`ValueError` naming the expected/actual pair *before* anything
        is constructed for or sent to the bus; (3) construct the
        ``CommandEnvelope`` with ``command_type=CANCEL_CODING_JOB``, the
        payload, and the caller-supplied identity fields verbatim
        (``idempotency_key`` is never regenerated or mutated — dedup is the
        bus/consumer's job); (4) ``await bus.publish(envelope)``, letting
        failures propagate (no retry, no swallow); (5) return the envelope.

        Returns:
            The published :class:`~sfp_contracts.commands.envelope.CommandEnvelope`,
            for inspection/assertion by the caller.
        """
        from sfp_contracts.commands.envelope import CommandType
        from sfp_contracts.commands.payloads import CancelCodingJob

        _check_payload_matches_discriminator(
            payload, CancelCodingJob, CommandType.CANCEL_CODING_JOB
        )
        envelope = self._build_envelope(
            payload,
            command_type=CommandType.CANCEL_CODING_JOB,
            message_id=message_id,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
            causation_id=causation_id,
            occurred_at=occurred_at,
        )
        await self._bus.publish(envelope)
        return envelope

    def _build_envelope(
        self,
        payload: CancelCodingJob,
        *,
        command_type: CommandType,
        message_id: str,
        idempotency_key: str,
        correlation_id: str,
        causation_id: str,
        occurred_at: str,
    ) -> CommandEnvelope:
        """Build the ``CommandEnvelope`` (or delegate to the factory)."""
        if self._envelope_factory is not None:
            return self._envelope_factory(payload)
        from sfp_contracts.commands.envelope import CommandEnvelope

        return CommandEnvelope(
            message_id=message_id,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
            causation_id=causation_id,
            occurred_at=occurred_at,
            command_type=command_type,
            payload=payload,
        )


class CancelReviewJobEmitter:
    """Publish the ``CANCEL_REVIEW_JOB`` command on the injected bus (SFP-245).

    An exact structural sibling of :class:`ExecuteCodingJobEmitter` (SFP-152).

    Fire condition: genuine-failure classification — a ``ShouldFailPolicy``
    terminal row — or an owner cancel while the review job is in flight. The
    emitter never reads failure classification to decide that (MAS §8.6): the
    caller emits once the classification or the cancel holds.

    Constructor-injected seams (identical to the SFP-152 emitter):

    - ``bus`` — the vendor-neutral ``MessageBus``; ``emit`` publishes exactly
      one ``CommandEnvelope`` per call.
    - ``envelope_factory`` — optional callable producing the
      ``CommandEnvelope``; when omitted, the emitter builds it from the
      caller-supplied identity fields verbatim.
    """

    def __init__(
        self,
        bus: MessageBus,
        *,
        envelope_factory: CommandEnvelopeFactory[CancelReviewJob] | None = None,
    ) -> None:
        self._bus = bus
        self._envelope_factory = envelope_factory

    async def emit(
        self,
        payload: CancelReviewJob,
        *,
        message_id: str,
        idempotency_key: str,
        correlation_id: str,
        causation_id: str,
        occurred_at: str,
    ) -> CommandEnvelope:
        """Construct and publish the ``CANCEL_REVIEW_JOB`` command envelope.

        Flow: (1) accept the caller's ``CancelReviewJob`` payload; (2) run
        the shared consistency check — a mismatched payload type raises
        :class:`ValueError` naming the expected/actual pair *before* anything
        is constructed for or sent to the bus; (3) construct the
        ``CommandEnvelope`` with ``command_type=CANCEL_REVIEW_JOB``, the
        payload, and the caller-supplied identity fields verbatim
        (``idempotency_key`` is never regenerated or mutated — dedup is the
        bus/consumer's job); (4) ``await bus.publish(envelope)``, letting
        failures propagate (no retry, no swallow); (5) return the envelope.

        Returns:
            The published :class:`~sfp_contracts.commands.envelope.CommandEnvelope`,
            for inspection/assertion by the caller.
        """
        from sfp_contracts.commands.envelope import CommandType
        from sfp_contracts.commands.payloads import CancelReviewJob

        _check_payload_matches_discriminator(
            payload, CancelReviewJob, CommandType.CANCEL_REVIEW_JOB
        )
        envelope = self._build_envelope(
            payload,
            command_type=CommandType.CANCEL_REVIEW_JOB,
            message_id=message_id,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
            causation_id=causation_id,
            occurred_at=occurred_at,
        )
        await self._bus.publish(envelope)
        return envelope

    def _build_envelope(
        self,
        payload: CancelReviewJob,
        *,
        command_type: CommandType,
        message_id: str,
        idempotency_key: str,
        correlation_id: str,
        causation_id: str,
        occurred_at: str,
    ) -> CommandEnvelope:
        """Build the ``CommandEnvelope`` (or delegate to the factory)."""
        if self._envelope_factory is not None:
            return self._envelope_factory(payload)
        from sfp_contracts.commands.envelope import CommandEnvelope

        return CommandEnvelope(
            message_id=message_id,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
            causation_id=causation_id,
            occurred_at=occurred_at,
            command_type=command_type,
            payload=payload,
        )


__all__ = [
    "CancelCodingJobEmitter",
    "CancelReviewJobEmitter",
    "ExecuteCodingJobEmitter",
    "NotifyUserEmitter",
    "RequestMergeEmitter",
    "RequestUserInputEmitter",
    "ReviewPullRequestEmitter",
    "SynchronizePullRequestEmitter",
]
