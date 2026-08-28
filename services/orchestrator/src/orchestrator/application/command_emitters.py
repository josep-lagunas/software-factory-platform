"""The application-layer command emitters (MAS §5.3 / §4.7, SFP-152).

Grounded in:
- MAS §5.3 — the command catalogue: commands are how the Orchestrator moves
  work *to* other agents; they carry intent, never state mutation.
- MAS §4.7 — every command rides a :class:`~sfp_contracts.messages.MessageEnvelope`
  of routing/dedup metadata plus a per-command payload.
- MAS §8.6 — the workflow advances only on events / user decisions; a command
  emitter never consults the workflow table and never transitions state.
- ID-031 — the authoritative envelope field set and command names.
- ID-072 — the Orchestrator *decides* and issues commands; the Workspace Worker
  *executes* them. Emitting is the decision's output, not the work itself.
- SFP-219 — :class:`~sfp_contracts.commands.envelope.CommandEnvelope` carries
  the ``command_type`` discriminator plus a typed payload; there is no
  envelope-level consistency validator.
- SFP-42 — the :class:`~sfp_messaging.bus.MessageBus` protocol is the only
  I/O seam this module touches.
- SFP-137 — the structural template: :class:`WorkflowTransitionPublisher`
  fixed the shape (constructor-injected bus, an optional envelope factory
  seam, validate/decide first then publish). This module mirrors it on the
  command side.
- SFP-45 (NOT landed) — the serde-layer envelope consistency check does not
  exist yet and is NOT relied upon: each emitter runs its own local
  payload/discriminator consistency check *before* any bus call.

Shape (per the PRSpec implementation notes):

- :class:`ExecuteCodingJobEmitter` is the first emitter (SFP-152). The sibling
  emitters (SFP-153..155) land as additive classes in this same module, each
  with its own payload/discriminator pair and no shared mutable state.
- ``emit(...)`` is a thin async method: build/accept the payload, run the
  local consistency check, construct the envelope, publish, return it.
- Purity discipline: no clock (``occurred_at`` is caller-supplied), no
  randomness, no I/O beyond the injected bus, no workflow-table access, no
  state transition. The emitter never invents or mutates envelope identity —
  ``idempotency_key`` is carried verbatim because dedup is the bus/consumer's
  job, not the emitter's.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from sfp_contracts.commands.envelope import CommandEnvelope
    from sfp_contracts.commands.payloads import ExecuteCodingJob
    from sfp_messaging.bus import MessageBus

    #: Seam: custom envelope construction, mirroring the
    #: ``WorkflowTransitionPublisher`` ``EnvelopeFactory`` seam (SFP-137).
    #: The deterministic emitter never invents ``message_id`` /
    #: ``idempotency_key`` / ``correlation_id`` / ``causation_id`` /
    #: ``occurred_at`` — they are caller-supplied or supplied by this factory.
    CommandEnvelopeFactory = Callable[[ExecuteCodingJob], CommandEnvelope]


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
        envelope_factory: CommandEnvelopeFactory | None = None,
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
        the local consistency check — a mismatched payload type raises
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
        self._check_payload_matches_discriminator(payload)
        envelope = self._build_envelope(
            payload,
            message_id=message_id,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
            causation_id=causation_id,
            occurred_at=occurred_at,
        )
        await self._bus.publish(envelope)
        return envelope

    def _check_payload_matches_discriminator(self, payload: object) -> None:
        """Assert the payload type matches the declared command discriminator.

        SFP-45 (the serde-layer envelope consistency check) is not landed and
        is not relied upon: this emitter-local check is the guard. It raises
        :class:`ValueError` naming both the expected and the actual payload
        type, and it runs *before* any bus call so a mismatched command never
        reaches the wire.
        """
        from sfp_contracts.commands.payloads import ExecuteCodingJob

        if type(payload) is not ExecuteCodingJob:
            raise ValueError(
                "EXECUTE_CODING_JOB command payload must be "
                f"{ExecuteCodingJob!r}, got {type(payload)!r}"
            )

    def _build_envelope(
        self,
        payload: ExecuteCodingJob,
        *,
        message_id: str,
        idempotency_key: str,
        correlation_id: str,
        causation_id: str,
        occurred_at: str,
    ) -> CommandEnvelope:
        """Build the ``CommandEnvelope`` (or delegate to the factory)."""
        if self._envelope_factory is not None:
            return self._envelope_factory(payload)
        from sfp_contracts.commands.envelope import CommandEnvelope, CommandType

        return CommandEnvelope(
            message_id=message_id,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
            causation_id=causation_id,
            occurred_at=occurred_at,
            command_type=CommandType.EXECUTE_CODING_JOB,
            payload=payload,
        )


__all__ = ["ExecuteCodingJobEmitter"]
