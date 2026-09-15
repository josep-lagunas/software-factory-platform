"""The Slack inbound consumer — the MAS §9.4 inbound half of Communication.

Consumes ``ExternalEventReceived`` events with ``source == "slack"`` off the
Message Bus, interprets the verbatim provider payload through a **local**
Slack provider schema, advances the ``UserInteraction``, and publishes
``UserQueryReceived`` or ``UserInputReceived`` per the ID-076 binding rule.

Grounded in:
- MAS §9.4 — the authoritative inbound flow: receive inbound communications,
  maintain interaction context, produce platform communication events. One
  ``UserInteraction`` maps 1:1 to a Slack thread in v0; "every inbound or
  outbound message updates ``last_message_emissor`` and
  ``last_message_timestamp``".
- MAS §9.2 / ID-076 — ALL ingress lives in the External Events Service: the
  sole inbound route is ``/webhooks/{endpoint_id}`` (SFP-120), which
  authenticates the raw body and publishes ``ExternalEventReceived`` carrying
  the parsed body **verbatim**. Communication never runs an HTTP ingress;
  this module is the bus consumer that replaces the drifted receiver of
  PR #163.
- ID-026 / ID-041 — payload opacity: the owning service interprets the body
  via its own local schema. Provider-schema interpretation for Slack lives
  HERE (:class:`SlackProviderMessage`), never in ingress.
- ID-076 (owner decision 2026-09-12) — the binding routing rule: input
  arriving in an interaction with ``response_required=true`` (an open
  platform question) → ``UserQueryReceived``; input in an interaction
  without an open question → ``UserInputReceived``. The discriminator is
  the SFP-112 model's ``response_required`` column. The SFP-244 interpreter
  stays landed but UNCONSUMED — this module never imports it — and the bot
  posts nothing on its own (outbound is platform-initiated only,
  SFP-133/135/136).
- SFP-124 — redeliveries are already collapsed upstream by the ingress's
  deterministic ``idempotency_key`` (``source:sha256(body)``); this consumer
  keeps NO dedupe state (stateless by design).
- SFP-257 (live-smoke findings, rounds 1+2, 2026-09-14) — an inbound event
  that is not NEW user input is ECHO and must never advance an interaction.
  Round 1: the app subscribing to whole-channel ``message`` events receives
  its own outbound posts back (``subtype="bot_message"`` / a ``bot_id``) —
  a bot-marker blacklist stopped the ~142-posts-in-~90s self-reply loop.
  Round 2 proved a blacklist structurally incomplete: a bot reply posted
  into a thread ALSO makes Slack emit a ``message_replied`` sub-event for
  the PARENT message, which carries the HUMAN's ``user`` id and NO bot
  markers — one human message re-summarized on every bot reply (measured:
  9 GLM calls / 8 posts over ~6 minutes). The guard is therefore a
  WHITELIST enforced ONCE at interpretation time in
  :func:`parse_slack_message` — the single place the provider body is read,
  never re-derived per handler: a message counts as user input ONLY when it
  carries NO ``subtype`` and NO ``bot_id``.
- SFP-129 — ``InteractionService.create`` is the find-or-create seam (by
  provider thread reference). The 8-hour ``expires_at`` RESET on subsequent
  messages is SFP-130, NOT here: this module updates only the two
  ``last_message_*`` fields MAS §9.4 names.
- SFP-43 — the ``@event_handler`` registry the handler below registers
  into; :class:`~sfp_messaging.transport.in_memory.InMemoryTransport`
  resolves dispatch by payload type — no ``bus.subscribe`` anywhere.

Terminal interactions (AP-005 / MAS §9.4 "Closed Interactions"): a reply
arriving on a COMPLETED or EXPIRED interaction surfaces as
:class:`~communication.application.interaction_service.\
InteractionTransitionError` from ``create()`` — it propagates, uncaught. The
 MAS-mandated "request the user to start a new thread" handling is SFP-131's
 (not yet landed); swallowing or re-routing the error here would invent that
 policy.

Envelope discipline (mirrored from ``interaction_service`` / SFP-124): the
consumer never invents identity — ``message_id`` / ``idempotency_key`` /
``correlation_id`` / ``causation_id`` / ``occurred_at`` come from the
injectable envelope factories, whose reference implementations derive
``idempotency_key`` deterministically from the event identity so a
republished fact dedupes by construction.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, ClassVar, Final, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sfp_contracts.events import (
    ExternalEventReceived,
    UserInputReceived,
    UserQueryReceived,
)
from sfp_contracts.events.envelope import EventEnvelope, EventType
from sfp_messaging import MessageContext, event_handler
from sqlalchemy import select

from communication.infrastructure.persistence import UserInteraction

if TYPE_CHECKING:
    # Annotation-only: importing ``interaction_service`` at runtime here
    # executes ``communication.application.__init__``, which imports
    # ``confirm_flow``, which imports THIS module — an import cycle that
    # breaks ``import communication.interfaces`` depending on entry order.
    from sfp_messaging.bus import MessageBus

    from communication.application.interaction_service import (
        InteractionService,
        SessionFactory,
    )

__all__ = [
    "SLACK_SOURCE",
    "SlackInboundConsumer",
    "SlackProviderMessage",
    "UserInputReceivedEnvelopeFactory",
    "UserQueryReceivedEnvelopeFactory",
    "handle_external_event_received",
    "make_user_input_received_envelope",
    "make_user_query_received_envelope",
    "parse_slack_message",
    "set_slack_inbound_consumer",
]

#: The ``source`` value this consumer handles (MAS §9.4: "ExternalEventReceived
#: where provider is Slack"). Any other source returns early — no schema
#: interpretation, no interaction write, no publish.
SLACK_SOURCE: Final[str] = "slack"

#: The ``producer`` stamped on every envelope the reference factories build —
#: the Communication Service produces ``UserInputReceived`` /
#: ``UserQueryReceived`` (MAS §5.4 / §9.4).
_PRODUCER: Final[str] = "communication"

#: Slack Events API top-level payload type that wraps one event object.
_EVENT_CALLBACK: Final[str] = "event_callback"

#: Event types that carry a human message the interaction maps to (the same
#: two the Slack app subscribes to for the ops channel; anything else —
#: reactions, membership changes, subtypes without the handled shape — is not
#: a message and is ignored).
_HANDLED_EVENT_TYPES: Final[frozenset[str]] = frozenset({"app_mention", "message"})

#: Slack's ``subtype`` for a message authored by a bot. The app's own
#: ``chat.postMessage`` deliveries echo back with exactly this marker (plus a
#: ``bot_id``) when the app subscribes to whole-channel ``message`` events —
#: the SFP-257 self-loop discriminator.
_BOT_MESSAGE_SUBTYPE: Final[str] = "bot_message"

#: ``origin`` for an interaction an inbound message opens (MAS §9.4: "origin
#: indicates whether the interaction was initiated inbound or outbound").
_INBOUND_ORIGIN: Final[str] = "inbound"

#: ``type`` label for an inbound-opened interaction — the vocabulary of the
#: event it produces (``UserInputReceived``), mirroring the landed handlers'
#: ``"user_input_request"`` / ``"notification"`` labels.
_INBOUND_INTERACTION_TYPE: Final[str] = "user_input"

#: Who sent the most recent message (MAS §9.4 canonical term): an inbound
#: Slack message is from the user.
_INBOUND_EMISSOR: Final[str] = "user"

#: The single injectable clock seam (MAS §12.7): zero-argument callable
#: returning the current timezone-aware UTC datetime — the ONLY wall-clock
#: read, used for ``last_message_timestamp``.
Clock = Callable[[], datetime]


# --------------------------------------------------------------------------- #
# The local Slack provider schema (ID-026 / ID-041 — interpretation lives here)
# --------------------------------------------------------------------------- #


class SlackProviderMessage(BaseModel):
    """The owning service's view of ONE Slack message delivery.

    Extracted from the **verbatim** Events API body carried on
    :class:`~sfp_contracts.events.ExternalEventReceived.payload` (SFP-124):
    ``extra="ignore"`` because the provider body legitimately carries many
    fields beyond the ones Communication needs (``team_id``, ``event_ts``,
    ``channel_type``, …) — the schema names what it uses and drops the rest.

    Fields:
        type: The inner event type (``app_mention`` / ``message``).
        text: The message text — carried verbatim onto the published event.
        channel: The Slack channel id the message was posted in.
        ts: The message's own timestamp id (Slack's message identity).
        user: The Slack user id of the sender (a provider identity — it is
            NOT mapped to an SFP ``user_id``; identity belongs to the
            Identity Service, MAS §9.4 "never owns user identity").
        thread_ts: The root message's ``ts`` for a thread reply, or ``None``
            for a top-level message (which is its own thread root).
        subtype: The Slack message subtype — ``None`` for a plain message
            (the ONLY shape that counts as new user input, SFP-257).
            Sub-typed events are Slack rendering state on top of an existing
            message, never new input: ``bot_message`` (a bot's post echoing
            back), ``message_replied`` (the PARENT-message sub-event every
            bot reply into a thread emits — it carries the parent HUMAN's
            ``user`` id and no bot markers), ``message_changed`` /
            ``message_deleted`` (edits), ``thread_broadcast`` (a broadcast
            duplicate of a reply already delivered as a plain event),
            ``channel_topic``, ``tombstone``, join/leave notifications.
        bot_id: The id of the bot that authored the message, when one did;
            ``None`` for human messages.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")

    type: str = Field(min_length=1)
    text: str = Field(min_length=1)
    channel: str = Field(min_length=1)
    ts: str = Field(min_length=1)
    user: str = Field(min_length=1)
    thread_ts: str | None = None
    subtype: str | None = None
    bot_id: str | None = None

    @property
    def provider_reference(self) -> str:
        """The 1:1 provider thread reference (MAS §9.4 / SFP-112).

        A thread reply carries ``thread_ts`` pointing at its root; a
        top-level message IS its own thread root, identified by its own
        ``ts``. Either way the reference is the thread's root ``ts`` — the
        value that groups a thread's replies into one ``UserInteraction``.
        """
        return self.thread_ts or self.ts

    @property
    def is_bot_authored(self) -> bool:
        """True when a bot authored this message — ANY bot, the app included.

        The round-1 SFP-257 discriminator: the app's own outbound posts echo
        back as ``subtype="bot_message"`` with a ``bot_id`` when the app
        subscribes to whole-channel ``message`` events. Such echo is not
        user input and must never advance an interaction.
        """
        return self.subtype == _BOT_MESSAGE_SUBTYPE or bool(self.bot_id)

    @property
    def is_user_input(self) -> bool:
        """True when this event is NEW user input — the whitelist (SFP-257).

        Only a PLAIN message counts: ``subtype`` absent AND ``bot_id``
        absent (``user`` presence is schema-enforced). Round 2 of the live
        smoke proved the bot-marker blacklist insufficient — a ``message_
        replied`` parent echo carries the human's ``user`` id and no bot
        markers yet is not new input. Anything sub-typed or bot-authored is
        Slack state rendered on top of an existing message: echo, not input.
        """
        return self.subtype is None and not self.is_bot_authored


class _SlackEventsCallback(BaseModel):
    """The verbatim top-level Events API body — only the routing field named.

    ``extra="ignore"`` (verbatim provider body); ``event`` is optional so a
    body without one (e.g. Slack's ``url_verification`` handshake) parses
    and is then filtered by :func:`parse_slack_message`, rather than being
    mistaken for a malformed delivery.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")

    type: str = Field(min_length=1)
    event: SlackProviderMessage | None = None


def parse_slack_message(payload: dict[str, Any]) -> SlackProviderMessage | None:
    """Parse a verbatim Slack payload; ``None`` when it is not a message.

    Deterministic and total: a body that is not an ``event_callback``
    envelope wrapping a handled event type (``app_mention`` / ``message``)
    with the handled shape — non-empty ``text`` / ``channel`` / ``ts`` /
    ``user`` — yields ``None``, never an exception. The user-input
    WHITELIST (SFP-257, enforced HERE at the single interpretation point
    rather than re-derived per handler): a ``message`` counts as new user
    input ONLY when it carries NO ``subtype`` and NO ``bot_id`` — anything
    else is Slack rendering state on top of an existing message (the app's
    own ``bot_message`` echo; the ``message_replied`` parent sub-event every
    bot reply into a thread emits, which carries the HUMAN's ``user`` id
    with no bot markers; ``message_changed`` / ``message_deleted`` edits;
    ``thread_broadcast`` duplicates; ``channel_topic``; ``tombstone``;
    join/leave) and yields ``None``. The caller treats ``None`` as "not a
    message this consumer maps to an interaction": no schema interpretation
    beyond this point, no write, no publish.

    Args:
        payload: The ``ExternalEventReceived.payload`` dict — the verbatim
            parsed provider body as published by the SFP-120 webhook.

    Returns:
        The extracted message, or ``None`` for anything unhandled.
    """
    try:
        body = _SlackEventsCallback.model_validate(payload)
    except ValidationError:
        return None
    if body.type != _EVENT_CALLBACK or body.event is None:
        return None
    if body.event.type not in _HANDLED_EVENT_TYPES:
        return None
    if not body.event.is_user_input:
        return None
    return body.event


# --------------------------------------------------------------------------- #
# Envelope factories (SFP-124 seam discipline — identity is runtime policy)
# --------------------------------------------------------------------------- #


class UserQueryReceivedEnvelopeFactory(Protocol):
    """Seam: envelope construction for ``UserQueryReceived`` events.

    Identity (``message_id`` / ``idempotency_key`` / ``correlation_id`` /
    ``causation_id`` / ``occurred_at``) is runtime policy — the consumer
    never invents it. A factory must derive ``idempotency_key``
    deterministically from the event identity so republishing the same fact
    produces the same key (dedupe by construction, the consumer is stateless).
    """

    def __call__(self, event: UserQueryReceived) -> EventEnvelope:
        """Return the envelope for one user-query event."""
        ...  # pragma: no cover


class UserInputReceivedEnvelopeFactory(Protocol):
    """Seam: envelope construction for ``UserInputReceived`` events."""

    def __call__(self, event: UserInputReceived) -> EventEnvelope:
        """Return the envelope for one user-input event."""
        ...  # pragma: no cover


def make_user_query_received_envelope(event: UserQueryReceived) -> EventEnvelope:
    """Reference factory: deterministic idempotency (SFP-124 mirror).

    ``idempotency_key`` derives from the event identity —
    ``user-query:{session_id}:{query}`` — so republishing the same query in
    the same thread maps to the same key. ``message_id`` / ``occurred_at``
    are fresh per invocation: a republish is a *new* message carrying the
    same fact. ``correlation_id`` anchors on the interaction's thread
    reference; ``causation_id`` is empty (the causing external event's id,
    if any, is runtime policy an injected factory may thread through).
    """
    return EventEnvelope(
        message_id=f"evt-{uuid4()}",
        idempotency_key=f"user-query:{event.session_id}:{event.query}",
        correlation_id=event.session_id,
        causation_id="",
        occurred_at=datetime.now(UTC).isoformat(),
        event_type=EventType.USER_QUERY_RECEIVED,
        producer=_PRODUCER,
        payload=event,
    )


def make_user_input_received_envelope(event: UserInputReceived) -> EventEnvelope:
    """Reference factory for ``UserInputReceived`` — the SFP-124 mirror.

    Identical discipline to :func:`make_user_query_received_envelope`, with
    ``idempotency_key = user-input:{session_id}:{text}``.
    """
    return EventEnvelope(
        message_id=f"evt-{uuid4()}",
        idempotency_key=f"user-input:{event.session_id}:{event.text}",
        correlation_id=event.session_id,
        causation_id="",
        occurred_at=datetime.now(UTC).isoformat(),
        event_type=EventType.USER_INPUT_RECEIVED,
        producer=_PRODUCER,
        payload=event,
    )


# --------------------------------------------------------------------------- #
# The consumer
# --------------------------------------------------------------------------- #


class SlackInboundConsumer:
    """Consume ``ExternalEventReceived(source="slack")`` per MAS §9.4.

    One consumed message runs the MAS §9.4 inbound sequence:

    1. **Filter** — ``source != "slack"`` returns early (no interpretation,
       no write, no publish).
    2. **Interpret** — the verbatim payload through the local
       :func:`parse_slack_message`; an unhandled body — or any sub-typed /
       bot-authored echo (SFP-257 whitelist) — returns early.
    3. **Advance** — find-or-create the ``UserInteraction`` by provider
       thread reference via :meth:`InteractionService.create
       <communication.application.interaction_service.InteractionService.create>`
       (an inbound-first message opens the interaction with
       ``origin="inbound"`` and ``response_required=False`` — there is no
       open platform question), then update the two ``last_message_*``
       fields MAS §9.4 names. ``expires_at`` is NOT reset here — the 8-hour
       reset on subsequent messages is SFP-130.
    4. **Publish** — the ID-076 binding rule on the SFP-112
       ``response_required`` column: ``True`` (open question) →
       ``UserQueryReceived``; ``False`` → ``UserInputReceived``; each event
       carries the extracted message text and the thread reference as
       ``session_id`` (v0: session_id IS the provider thread reference).

    Constructor-injected seams:

    - ``bus`` — the vendor-neutral :class:`~sfp_messaging.bus.MessageBus`
      the routed event is published onto (in-memory today; SFP-118/101
      re-plumb).
    - ``interaction_service`` — the SFP-129 application service (the ONLY
      find-or-create path; this consumer never inserts rows itself).
    - ``session_factory`` — one unit of work for the ``last_message_*``
      update (:data:`SessionFactory`; see ``session_scope``).
    - ``clock`` — the ONLY wall-clock read (MAS §12.7), defaulting to
      ``datetime.now(UTC)``; tests inject a fixed fake.
    - ``query_envelope_factory`` / ``input_envelope_factory`` — the
      envelope seams for the two publish branches (SFP-124 discipline),
      defaulting to the reference factories above.

    The consumer makes **no outbound Slack calls anywhere** (posting is
    platform-initiated only — SFP-133/135/136) and imports **no
    interpreter** (SFP-244 stays unconsumed, ID-076).
    """

    def __init__(
        self,
        *,
        bus: MessageBus,
        interaction_service: InteractionService,
        session_factory: SessionFactory,
        clock: Clock | None = None,
        query_envelope_factory: UserQueryReceivedEnvelopeFactory | None = None,
        input_envelope_factory: UserInputReceivedEnvelopeFactory | None = None,
    ) -> None:
        self._bus = bus
        self._interaction_service = interaction_service
        self._session_factory = session_factory
        self._clock: Clock = clock or (lambda: datetime.now(UTC))
        self._query_envelope_factory = query_envelope_factory or make_user_query_received_envelope
        self._input_envelope_factory = input_envelope_factory or make_user_input_received_envelope

    async def consume(self, event: ExternalEventReceived) -> None:
        """Run one external event through the MAS §9.4 inbound sequence.

        Any unhandled input — a non-Slack source or a payload that is not a
        handled Slack message — returns without side effects. A reply to a
        terminal interaction propagates
        :class:`~communication.application.interaction_service.\
InteractionTransitionError` from ``create()`` (AP-005; closed-handling is
        SFP-131's, not invented here).

        Args:
            event: The bus event published by the external-events ingress
                (SFP-120/124) — ``payload`` is the verbatim provider body.
        """
        if event.source != SLACK_SOURCE:
            return
        message = parse_slack_message(event.payload)
        if message is None:
            return

        provider_reference = message.provider_reference
        interaction = await self._interaction_service.create(
            provider_reference,
            channel=SLACK_SOURCE,
            origin=_INBOUND_ORIGIN,
            interaction_type=_INBOUND_INTERACTION_TYPE,
            question=message.text,
            response_required=False,
        )
        self._record_inbound_message(provider_reference)

        if interaction.response_required:
            envelope = self._query_envelope_factory(
                UserQueryReceived(session_id=provider_reference, query=message.text)
            )
        else:
            envelope = self._input_envelope_factory(
                UserInputReceived(session_id=provider_reference, text=message.text)
            )
        await self._bus.publish(envelope)

    def _record_inbound_message(self, provider_reference: str) -> None:
        """Update the two ``last_message_*`` fields MAS §9.4 names.

        One unit of work over the SFP-112 row ``InteractionService.create``
        just ensured exists: ``last_message_emissor="user"`` and
        ``last_message_timestamp`` to the injected clock's now. ``expires_at``
        is deliberately untouched (the inactivity-window reset is SFP-130).
        The lookup mirrors the service's deterministic newest-first ordering.
        """
        now = self._clock()
        with self._session_factory() as session:
            interaction = session.scalars(
                select(UserInteraction)
                .where(UserInteraction.provider_reference == provider_reference)
                .order_by(UserInteraction.created_at.desc())
                .limit(1)
            ).first()
            if interaction is None:  # pragma: no cover — create() just ran
                raise LookupError(
                    f"No UserInteraction found for provider_reference={provider_reference!r}"
                )
            interaction.last_message_emissor = _INBOUND_EMISSOR
            interaction.last_message_timestamp = now


# --- Event handler (SFP-43 registry; no HTTP, no Slack I/O, no interpreter) ---

_bound_consumer: SlackInboundConsumer | None = None


def set_slack_inbound_consumer(consumer: SlackInboundConsumer | None) -> None:
    """Bind the wired :class:`SlackInboundConsumer` the event handler uses.

    Composition-root responsibility: construct the consumer with its
    bus/service/session/clock seams and bind it once at startup.
    Last-write-wins so tests can rebind (or unbind with ``None``) per case.
    """
    global _bound_consumer
    _bound_consumer = consumer


def _require_consumer() -> SlackInboundConsumer:
    """Return the bound consumer or raise the pinned not-wired error."""
    if _bound_consumer is None:
        raise RuntimeError(
            "No SlackInboundConsumer bound — call set_slack_inbound_consumer(consumer) "
            "from the composition root before dispatching events"
        )
    return _bound_consumer


@event_handler(ExternalEventReceived)
async def handle_external_event_received(
    payload: ExternalEventReceived, context: MessageContext
) -> None:
    """Registry-dispatched entry point for ``ExternalEventReceived``.

    Registered via ``@event_handler`` so :class:`~sfp_messaging.transport.\
in_memory.InMemoryTransport` resolves dispatch by payload type (SFP-43) —
    never ``bus.subscribe``. Pure delegation to the bound consumer: the
    source filter and provider-schema interpretation happen inside
    :meth:`SlackInboundConsumer.consume`. The context is accepted per the
    handler shape (SFP-43/44) and unused.
    """
    await _require_consumer().consume(payload)
