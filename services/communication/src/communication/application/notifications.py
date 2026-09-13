"""The Communication outbound leg — notification command handlers (SFP-136).

Consumes the cross-service Communication commands ``NotifyUser`` and
``RequestUserInput`` (MAS §9.4 outbound leg) and delivers them to the
recipient's Slack destination:

1. resolve the destination via the injected
   :class:`SlackDestinationResolver` Protocol (the session → channel/thread
   mapping; SFP-128 owns the concrete Identity-backed implementation),
2. deliver the message verbatim through the injected
   :class:`~communication.interfaces.outbound.OutboundMessagePort` (the
   landed SFP-133 seam — no Slack HTTP here),
3. for ``RequestUserInput``, find-or-create the ``UserInteraction`` FIRST
   via :meth:`~communication.application.interaction_service.\
InteractionService.create` (``origin="outbound"``,
   ``response_required=True``, ``question = command.prompt``,
   ``provider_reference = command.session_id``), THEN deliver the prompt.

Grounded in:
- MAS §9.4 — the outbound leg: a solicitation/notification goes out on a
  (possibly new) interaction thread; one interaction ↔ one Slack thread.
- MAS §11.8 (immediate emission) — Communication commands bypass the
  Scheduler/queue/admission entirely: the module imports no scheduler, no
  queue, no admission symbol anywhere. Delivery is immediate, in-process.
- MAS §12.7 (determinism) — no LLM import (no ``AgentRuntime``), no
  wall-clock read: message text arrives verbatim from the command payload
  and the injected :class:`~communication.application.interaction_service.\
InteractionService` owns its own clock.
- SFP-134 (the ``InteractionSummaryWriter`` deferral idiom) — the concrete
  ``SlackDestinationResolver`` lands with SFP-128 (the Identity read-only
  query API); this module depends ONLY on the runtime-checkable Protocol,
  pinned by fakes. **No symbol of the identity service is imported
  anywhere in this module**, and no concrete resolver either.
- SFP-133 error partition — transport failure raises
  :class:`~communication.interfaces.outbound.ProviderError`; any
  provider-reported failure arrives as a non-ok
  :class:`~communication.interfaces.outbound.DeliveryReceipt`. Both map to
  the typed :class:`NotificationFailed` outcome — never swallowed, never
  converted into workflow or interaction corruption. The
  ``RequestUserInput`` interaction row persists across a failed delivery
  **by design**: find-or-create makes redelivery of the same command
  idempotent (the repeat returns the existing row, no second insert).

Outcomes are frozen pydantic models (house idiom): :class:`NotificationDelivered`
carries the ``DeliveryReceipt``; :class:`NotificationFailed` carries the typed
error (the provider error code from a non-ok receipt, or the exception
message of a ``ProviderError``).
"""

from __future__ import annotations

from typing import ClassVar, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field
from sfp_contracts.commands import NotifyUser, RequestUserInput

from communication.application.interaction_service import InteractionService
from communication.interfaces.outbound import (
    DeliveryReceipt,
    OutboundMessagePort,
    ProviderError,
)

__all__ = [
    "NotificationDelivered",
    "NotificationFailed",
    "NotificationOutcome",
    "NotificationService",
    "SlackDestination",
    "SlackDestinationResolver",
]


class SlackDestination(BaseModel):
    """Where an outbound message for a session must land (SFP-128 shape).

    Opaque ref strings exactly as the
    :class:`~communication.interfaces.outbound.OutboundMessagePort` seam
    expects — the provider-native mapping is the adapter's concern.

    Frozen: a resolved destination is a fact of this delivery, never rewritten.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    channel_ref: str = Field(min_length=1)
    thread_ref: str | None = None


@runtime_checkable
class SlackDestinationResolver(Protocol):
    """Port: session id → Slack destination (the SFP-134 deferral idiom).

    A narrow, runtime-checkable Protocol with the single method the outbound
    leg needs. The CONCRETE implementation (backed by the Identity
    read-only query API) lands with SFP-128 and is deliberately NOT imported
    here — this module only ever sees an injected implementation, pinned by
    test fakes (the port keeps the seam swappable and this module free of
    any identity-service dependency).
    """

    def resolve(self, session_id: str) -> SlackDestination:
        """Return the Slack destination for ``session_id``.

        Raises: implementation-specific lookup errors (e.g. the session has
        no known Slack channel) — the handler does not catch them here; the
        destination lookup is Identity's contract (SFP-128).
        """
        ...  # pragma: no cover


class NotificationOutcome(BaseModel):
    """The abstract base of the typed delivery outcomes (house idiom)."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")


class NotificationDelivered(NotificationOutcome):
    """The message was accepted by the provider (``receipt.ok`` true)."""

    receipt: DeliveryReceipt


class NotificationFailed(NotificationOutcome):
    """Delivery failed — provider-reported (non-ok receipt) or transport.

    Exactly one of the two failure modes (the SFP-133 partition):

    - ``error`` — the provider's error code when the delivery produced a
      non-ok :class:`~communication.interfaces.outbound.DeliveryReceipt`;
    - ``transport_error`` — the message of the raised
      :class:`~communication.interfaces.outbound.ProviderError`.

    The handler raises neither onward nor swallows either: the caller gets
    the typed outcome and decides retry policy. The command carries no
    secret material in either field (ID-016 — carrier ids and status only).
    """

    error: str | None = None
    transport_error: str | None = None


class NotificationService:
    """The outbound-leg command handler (MAS §9.4, SFP-136).

    Constructor-injected seams only — no globals, no registry binding, no
    I/O of its own:

    - ``outbound`` — the provider-agnostic
      :class:`~communication.interfaces.outbound.OutboundMessagePort`
      (production: the landed ``SlackOutboundClient``).
    - ``interactions`` — the
      :class:`~communication.application.interaction_service.\
InteractionService` that owns the ``UserInteraction`` lifecycle (find-or-
      create here; its injected clock is the ONLY wall-clock in the chain).
    - ``resolver`` — a :class:`SlackDestinationResolver` implementation;
      the concrete one arrives with SFP-128.
    """

    def __init__(
        self,
        *,
        outbound: OutboundMessagePort,
        interactions: InteractionService,
        resolver: SlackDestinationResolver,
    ) -> None:
        self._outbound = outbound
        self._interactions = interactions
        self._resolver = resolver

    async def handle_notify_user(self, payload: NotifyUser) -> NotificationOutcome:
        """Deliver a ``NotifyUser`` message verbatim to its destination.

        Resolves the recipient via the injected resolver and posts
        ``payload.message`` unchanged — no transformation, no LLM (MAS §9.4:
        the message text comes verbatim from the command payload).
        """
        return await self._deliver(payload.session_id, payload.message)

    async def handle_request_user_input(self, payload: RequestUserInput) -> NotificationOutcome:
        """Find-or-create the interaction, then deliver the prompt verbatim.

        The interaction is created FIRST (``origin="outbound"``,
        ``response_required=True``, ``question = payload.prompt``,
        ``provider_reference = payload.session_id`` — the command's
        ``session_id`` IS the provider thread reference in v0, one
        interaction ↔ one Slack thread). Then the prompt is delivered.
        Find-or-create means a repeated command for the same session
        reuses the existing interaction — redelivery after a failed send
        is idempotent, and the interaction row persists across a failed
        delivery BY DESIGN (it is the thread the eventual reply lands on).
        """
        await self._interactions.create(
            payload.session_id,
            origin="outbound",
            interaction_type="user_input_request",
            question=payload.prompt,
            response_required=True,
        )
        return await self._deliver(payload.session_id, payload.prompt)

    async def _deliver(self, session_id: str, text: str) -> NotificationOutcome:
        """Resolve the destination, send ``text``, and type the outcome.

        Both failure modes (the SFP-133 partition) map to
        :class:`NotificationFailed`; a non-ok receipt's provider error code
        becomes ``error``, a raised :class:`ProviderError`'s message becomes
        ``transport_error``.
        """
        destination = self._resolver.resolve(session_id)
        try:
            receipt = self._outbound.send_message(
                text,
                channel_ref=destination.channel_ref,
                thread_ref=destination.thread_ref,
            )
        except ProviderError as exc:
            return NotificationFailed(transport_error=str(exc))
        if receipt.ok:
            return NotificationDelivered(receipt=receipt)
        return NotificationFailed(error=receipt.error)
