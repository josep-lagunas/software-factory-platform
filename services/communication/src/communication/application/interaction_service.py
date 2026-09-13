"""The ``UserInteraction`` application service (MAS §9.4, SFP-129).

Owns the interaction lifecycle the SFP-112 persistence model describes:
``create`` (find-or-create by provider reference) and ``complete`` (the single
terminal write transition), with a **derived** status view and
``UserInteractionUpdated`` events on the injected
:class:`~sfp_messaging.bus.MessageBus`.

Grounded in:
- MAS §9.4 — the authoritative ``UserInteraction`` lifecycle. One interaction
  maps 1:1 to a Slack thread in v0; the service owns the communication
  lifecycle transitions.
- AP-005 (terminal immutability) — completed and expired interactions are
  immutable and never reopened; ``complete()`` on a terminal interaction raises
  the typed :class:`InteractionTransitionError`. Reopen/closed-handling is
  SFP-131, NOT here.
- AP-009 / MAS §9.4 — the 8-hour inactivity window anchors ``expires_at`` at
  creation. Expiry is NEVER a write transition: no job flips a column (there
  is none). ``record_message`` (SFP-135) IS the MAS §9.4 timer reset: every
  message re-anchors ``expires_at`` at its own timestamp + 8h, so an
  interaction that *would* derive ``EXPIRED`` derives ``ACTIVE`` again after
  the message — expiry is derived, never persisted.
- MAS §12.7 (determinism) — the only wall-clock read is the single injectable
  ``clock``; same inputs always yield the same output. No sleeps anywhere.
- SFP-124 — the envelope-factory seam discipline mirrored from
  ``external_events.application.publisher`` (and the sibling Slack endpoint's
  reference factory): identity (``message_id`` / ``idempotency_key`` /
  ``correlation_id`` / ``causation_id`` / ``occurred_at``) is runtime policy —
  the service never invents it.
- SFP-43 — the ``@command_handler`` registry the two command handlers below
  register into; handlers contain no Slack I/O (posting is SFP-133 /
  ``slack_outbound``).

Derived status — never a column (explicit SFP-129 out-of-scope):
``completed_at`` non-null → ``COMPLETED``; ``now > expires_at`` and not
completed → ``EXPIRED``; otherwise ``ACTIVE``. The SFP-112 model gains no
``status`` column and no migration is touched.

Find-or-create: ``create()`` queries by ``provider_reference``; an existing
NON-terminal interaction is returned unchanged (no second insert, no second
event); an existing TERMINAL interaction raises
:class:`InteractionTransitionError` (v0 has no reopen — SFP-131 owns
closed-handling); a miss inserts one row with ``expires_at = now + 8h`` and
publishes exactly one ``UserInteractionUpdated``. NOTE (v0, single-process):
``provider_reference`` carries no unique index in the SFP-112 schema — the
no-duplicates guarantee is this service's serialized lookup, not a DB
constraint.

Event mapping (the SFP-135 alignment): the pinned ``UserInteractionUpdated``
payload carries ``session_id`` + ``state``; ``session_id`` holds the provider
thread reference (one interaction ↔ one thread, MAS §9.4) and ``state`` the
derived status. The SFP-135 methods — ``update_summary`` (the concrete
:class:`~communication.application.communication_agent.\
InteractionSummaryWriter` port implementation) and ``record_message`` (the
expiry-timer reset) — follow the same session-scope + typed-error + one-event
idiom as ``create`` / ``complete``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Final, Protocol
from uuid import UUID, uuid4

from sfp_contracts.commands import NotifyUser, RequestUserInput
from sfp_contracts.events import UserInteractionUpdated
from sfp_contracts.events.envelope import EventEnvelope, EventType
from sfp_messaging import MessageContext, command_handler
from sqlalchemy import select
from sqlalchemy.orm import Session

from communication.infrastructure.persistence import UserInteraction

if TYPE_CHECKING:
    from sfp_messaging.bus import MessageBus

__all__ = [
    "InteractionService",
    "InteractionStatus",
    "InteractionTransitionError",
    "SessionFactory",
    "derive_status",
    "handle_notify_user",
    "handle_request_user_input",
    "make_user_interaction_updated_envelope",
    "session_scope",
    "set_interaction_service",
]


#: The ``producer`` stamped on every envelope the reference factory builds —
#: the Communication Service is the exclusive producer of
#: ``UserInteractionUpdated`` (MAS §5.4 / §9.4, matching the SFP-132 endpoint).
_PRODUCER: Final[str] = "communication"

#: The AP-009 / MAS §9.4 inactivity window that anchors ``expires_at`` at
#: creation. The 8h RESET timer on subsequent messages is SFP-130 — NOT here.
_INACTIVITY_WINDOW: Final[timedelta] = timedelta(hours=8)

#: Who opens an interaction created here: the agent side sends the first
#: outbound message (MAS §9.4 ``last_message_emissor``).
_OPENING_EMISSOR: Final[str] = "agent"

#: One unit of work: a callable yielding an open :class:`~sqlalchemy.orm.Session`
#: that commits on success and rolls back on error — the shape pinned by the
#: Orchestrator's SQLAlchemy adapter (SFP-137 seam pattern). A bare
#: ``sessionmaker`` does NOT satisfy it (a Session context manager closes
#: without committing); wrap it with :func:`session_scope`:
#: ``lambda: session_scope(sessionmaker(bind=engine, expire_on_commit=False))``.
#: ``expire_on_commit=False`` keeps instances the service returns readable
#: after the unit of work commits.
SessionFactory = Callable[[], AbstractContextManager[Session]]

#: The single injectable clock seam (MAS §12.7): zero-argument callable
#: returning the current timezone-aware UTC datetime.
Clock = Callable[[], datetime]


class InteractionStatus(StrEnum):
    """The three derived lifecycle statuses (MAS §9.4).

    A ``StrEnum`` so the member's plain name is its string value — the wire
    representation published as ``UserInteractionUpdated.state`` is exactly
    ``ACTIVE`` / ``COMPLETED`` / ``EXPIRED``, with no mapping layer between.
    """

    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    EXPIRED = "EXPIRED"


class InteractionTransitionError(Exception):
    """A lifecycle transition was refused (AP-005 terminal immutability).

    Raised by :meth:`InteractionService.complete` on a ``COMPLETED`` or
    ``EXPIRED`` interaction, and by :meth:`InteractionService.create` when the
    provider reference already resolves to a terminal interaction (no reopen in
    v0 — SFP-131 owns closed-handling). Carries the *derived* status that made
    the interaction immutable; ``repr`` includes it.
    """

    def __init__(
        self,
        derived_status: InteractionStatus,
        *,
        interaction_id: object | None = None,
    ) -> None:
        super().__init__(derived_status)
        self.derived_status = derived_status
        self.interaction_id = interaction_id

    def __repr__(self) -> str:
        return (
            f"InteractionTransitionError(derived_status={self.derived_status!r}, "
            f"interaction_id={self.interaction_id!r})"
        )


def derive_status(interaction: UserInteraction, *, now: datetime) -> InteractionStatus:
    """Derive the lifecycle status from the SFP-112 timestamps alone.

    Pure function (MAS §12.7): no I/O, no writes, no column exists to read —
    the ordering is authoritative:

    1. ``completed_at`` non-null → :attr:`InteractionStatus.COMPLETED`;
    2. else ``now > expires_at`` (strictly) → :attr:`InteractionStatus.EXPIRED`;
    3. else :attr:`InteractionStatus.ACTIVE` (``now == expires_at`` is still
       ACTIVE — the boundary belongs to the open window).

    Args:
        interaction: The persisted (or about-to-be-persisted) interaction.
        now: The clock reading to compare ``expires_at`` against — always the
            injected clock in production, a fixed literal in tests.

    Returns:
        The derived status.
    """
    if interaction.completed_at is not None:
        return InteractionStatus.COMPLETED
    if _as_utc(now) > _as_utc(interaction.expires_at):
        return InteractionStatus.EXPIRED
    return InteractionStatus.ACTIVE


def _as_utc(moment: datetime) -> datetime:
    """Normalize a timestamp to timezone-aware UTC for comparison.

    Production Postgres ``timestamptz`` round-trips aware datetimes and the
    service's clock is aware; a SQLite-backed session (the deterministic test
    transport, and any dev embed) round-trips NAIVE datetimes. Normalizing at
    this single seam keeps ``derive_status`` a pure total function over both —
    a naive timestamp is interpreted as UTC, the service's sole clock domain.
    """
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment


class UserInteractionUpdatedEnvelopeFactory(Protocol):
    """Seam: envelope construction for ``UserInteractionUpdated`` events.

    Mirrors the SFP-124 ``EventEnvelopeFactory`` discipline: identity
    (``message_id`` / ``idempotency_key`` / ``correlation_id`` /
    ``causation_id`` / ``occurred_at``) is runtime policy — the service never
    invents it. A factory must derive ``idempotency_key`` deterministically
    from the event identity so two publishes of the same
    ``(session_id, state)`` produce the same key and downstream dedupe works
    by construction.
    """

    def __call__(self, event: UserInteractionUpdated) -> EventEnvelope:
        """Return the envelope for one interaction-update event."""
        ...  # pragma: no cover


def make_user_interaction_updated_envelope(event: UserInteractionUpdated) -> EventEnvelope:
    """Reference envelope factory: deterministic idempotency (SFP-124 mirror).

    ``idempotency_key`` derives from the event identity —
    ``user-interaction:{session_id}:{state}`` — so republishing the same state
    for the same interaction maps to the same key (dedupe by construction, the
    service is stateless). ``message_id`` / ``occurred_at`` are fresh per
    invocation: a republish is a *new* message carrying the same fact.
    ``correlation_id`` anchors on the interaction's thread reference;
    ``causation_id`` is empty (the causing command's id, if any, is runtime
    policy an injected factory may thread through).
    """
    return EventEnvelope(
        message_id=f"evt-{uuid4()}",
        idempotency_key=f"user-interaction:{event.session_id}:{event.state}",
        correlation_id=event.session_id,
        causation_id="",
        occurred_at=datetime.now(UTC).isoformat(),
        event_type=EventType.USER_INTERACTION_UPDATED,
        producer=_PRODUCER,
        payload=event,
    )


@contextmanager
def session_scope(session_factory: Callable[[], Session]) -> Iterator[Session]:
    """Provide a transactional session scope (the SFP-137 unit-of-work helper).

    Commit on clean exit, roll back on any exception (including a raised
    :class:`InteractionTransitionError` — a refused transition persists
    nothing), always close.
    """
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


class InteractionService:
    """The ``UserInteraction`` application service (MAS §9.4, SFP-129).

    Constructor-injected seams:

    - ``bus`` — the vendor-neutral :class:`~sfp_messaging.bus.MessageBus`
      (in-memory today; SFP-118 / SFP-101 re-plumb). Every successful
      create/complete/update_summary/record_message publishes exactly one
      ``UserInteractionUpdated`` AFTER the unit of work commits, so a
      published event always reflects committed state (a bus failure after
      commit leaves the write standing — v0 has no outbox).
    - ``session_factory`` — one unit of work per operation
      (:data:`SessionFactory`; see :func:`session_scope`).
    - ``clock`` — the ONLY wall-clock read (MAS §12.7). Defaults to
      ``datetime.now(UTC)``; tests inject a fixed/advancing fake so EXPIRED is
      reached by moving the fake, never by sleeping.
    - ``envelope_factory`` — optional envelope seam (SFP-124 discipline);
      defaults to :func:`make_user_interaction_updated_envelope`.
    """

    def __init__(
        self,
        *,
        bus: MessageBus,
        session_factory: SessionFactory,
        clock: Clock | None = None,
        envelope_factory: UserInteractionUpdatedEnvelopeFactory | None = None,
    ) -> None:
        self._bus = bus
        self._session_factory = session_factory
        self._clock: Clock = clock or (lambda: datetime.now(UTC))
        self._envelope_factory = envelope_factory or make_user_interaction_updated_envelope

    async def create(
        self,
        provider_reference: str,
        *,
        channel: str = "slack",
        origin: str = "outbound",
        interaction_type: str = "question",
        question: str = "",
        response_required: bool = False,
        user_id: UUID | None = None,
    ) -> UserInteraction:
        """Find-or-create the interaction for ``provider_reference`` (MAS §9.4).

        - Found and non-terminal → return it UNCHANGED: no second insert, no
          event (find-or-create idempotency).
        - Found and terminal → raise :class:`InteractionTransitionError`
          (AP-005; no reopen in v0 — SFP-131 owns closed-handling).
        - Not found → insert one row (``expires_at = now + 8h`` per AP-009,
          ``last_message_timestamp = now``, summary initialized to the opening
          ``question``) and publish exactly one ``UserInteractionUpdated``
          with the derived status ``ACTIVE``.

        Args:
            provider_reference: The 1:1 provider thread reference (v0: the
                Slack thread ts / channel — the command ``session_id``).
            channel: Provider channel (v0: ``"slack"``).
            origin: ``"inbound"`` or ``"outbound"`` — this service opens
                interactions outbound (a solicitation/notification goes out).
            interaction_type: The MAS §9.4 type/category label.
            question: The communication objective that opens the interaction.
            response_required: Whether a user response is required.
            user_id: Optional resolvable user identifier.

        Returns:
            The existing or newly inserted interaction.
        """
        now = self._clock()
        with self._session_factory() as session:
            existing = self._load(session, provider_reference)
            if existing is not None:
                state = derive_status(existing, now=now)
                if state is not InteractionStatus.ACTIVE:
                    raise InteractionTransitionError(state, interaction_id=existing.interaction_id)
                return existing
            interaction = UserInteraction(
                user_id=user_id,
                origin=origin,
                type=interaction_type,
                response_required=response_required,
                channel=channel,
                provider_reference=provider_reference,
                question=question,
                summary=question,
                last_message_emissor=_OPENING_EMISSOR,
                last_message_timestamp=now,
                expires_at=now + _INACTIVITY_WINDOW,
            )
            session.add(interaction)
        # Unit of work committed; the event reflects committed state.
        await self._publish_updated(provider_reference, InteractionStatus.ACTIVE)
        return interaction

    async def complete(self, provider_reference: str) -> UserInteraction:
        """Complete the ACTIVE interaction for ``provider_reference``.

        The single terminal write transition: sets ``completed_at`` to the
        injected clock's now, commits, and publishes one
        ``UserInteractionUpdated`` with the derived status ``COMPLETED``.

        Args:
            provider_reference: The 1:1 provider thread reference.

        Returns:
            The completed interaction (``completed_at`` set).

        Raises:
            LookupError: No interaction exists for the reference.
            InteractionTransitionError: The interaction is already terminal —
                its ``derived_status`` is ``COMPLETED`` or ``EXPIRED``
                (AP-005; nothing is persisted, nothing is published).
        """
        now = self._clock()
        with self._session_factory() as session:
            interaction = self._load(session, provider_reference)
            if interaction is None:
                raise LookupError(
                    f"No UserInteraction found for provider_reference={provider_reference!r}"
                )
            state = derive_status(interaction, now=now)
            if state is not InteractionStatus.ACTIVE:
                raise InteractionTransitionError(state, interaction_id=interaction.interaction_id)
            interaction.completed_at = now
        # Unit of work committed; the event reflects committed state.
        await self._publish_updated(provider_reference, InteractionStatus.COMPLETED)
        return interaction

    async def update_summary(self, provider_reference: str, summary: str) -> None:
        """Persist the updated durable summary for the interaction (AP-009).

        The concrete implementation of the
        :class:`~communication.application.communication_agent.\
InteractionSummaryWriter` port (SFP-134) — the one lifecycle effect the
        :class:`~communication.application.communication_agent.CommunicationAgent`
        delegates. Sets ``summary`` on the interaction, commits, and publishes
        one ``UserInteractionUpdated`` with the derived status (``ACTIVE`` on
        an open interaction). The summary is the durable representation —
        NEVER a transcript (AP-009).

        Args:
            provider_reference: The 1:1 provider thread reference.
            summary: The non-empty durable summary text to persist.

        Raises:
            LookupError: No interaction exists for the reference (fail-closed
                — no silent no-op).
            InteractionTransitionError: The interaction is terminal — its
                derived status is ``COMPLETED`` or ``EXPIRED`` (AP-005
                terminal immutability; a summary write never reopens or
                mutates a closed interaction). Nothing is persisted, nothing
                is published.
        """
        now = self._clock()
        with self._session_factory() as session:
            interaction = self._load(session, provider_reference)
            if interaction is None:
                raise LookupError(
                    f"No UserInteraction found for provider_reference={provider_reference!r}"
                )
            state = derive_status(interaction, now=now)
            if state is not InteractionStatus.ACTIVE:
                raise InteractionTransitionError(state, interaction_id=interaction.interaction_id)
            interaction.summary = summary
        # Unit of work committed; the event reflects committed state.
        await self._publish_updated(provider_reference, InteractionStatus.ACTIVE)

    async def record_message(
        self,
        provider_reference: str,
        emissor: str,
        timestamp: datetime,
    ) -> None:
        """Record one message and reset the inactivity timer (MAS §9.4).

        "Every inbound or outbound message updates ``last_message_emissor``
        and ``last_message_timestamp``" — and because the 8-hour window is
        *measured from* ``last_message_timestamp`` (AP-009 / the SFP-112
        column comment), the write re-anchors ``expires_at`` at
        ``timestamp + 8h``. Determinism (MAS §12.7): the timestamp is
        CARRIED by the caller (the Slack message ``ts``, or any injected
        value) — this method reads no wall clock for the message instant.
        After the write an interaction that *would* derive ``EXPIRED``
        derives :attr:`InteractionStatus.ACTIVE` again (expiry is derived,
        never persisted), and exactly one ``UserInteractionUpdated`` is
        published with the re-derived status.

        Args:
            provider_reference: The 1:1 provider thread reference.
            emissor: Who sent the message (the ``user`` / ``agent`` vocabulary).
            timestamp: The message's instant — carried, never read from the
                wall clock here (MAS §12.7).

        Raises:
            LookupError: No interaction exists for the reference (fail-closed
                — no silent no-op).
        """
        with self._session_factory() as session:
            interaction = self._load(session, provider_reference)
            if interaction is None:
                raise LookupError(
                    f"No UserInteraction found for provider_reference={provider_reference!r}"
                )
            interaction.last_message_emissor = emissor
            interaction.last_message_timestamp = timestamp
            interaction.expires_at = timestamp + _INACTIVITY_WINDOW
            state = derive_status(interaction, now=self._clock())
        # Unit of work committed; the event reflects committed state.
        await self._publish_updated(provider_reference, state)

    async def status(self, provider_reference: str) -> InteractionStatus:
        """Return the DERIVED status of the interaction for the reference.

        Read-only: computes :func:`derive_status` against the injected clock —
        no write, no event, no status column exists to read.

        Args:
            provider_reference: The 1:1 provider thread reference.

        Returns:
            The derived status.

        Raises:
            LookupError: No interaction exists for the reference.
        """
        now = self._clock()
        with self._session_factory() as session:
            interaction = self._load(session, provider_reference)
            if interaction is None:
                raise LookupError(
                    f"No UserInteraction found for provider_reference={provider_reference!r}"
                )
            return derive_status(interaction, now=now)

    @staticmethod
    def _load(session: Session, provider_reference: str) -> UserInteraction | None:
        """Fetch the interaction for ``provider_reference``, or ``None``.

        Deterministic ordering (``created_at`` descending) so the lookup stays
        stable even if duplicate references ever existed; v0 single-process
        usage makes this a single row in practice.
        """
        stmt = (
            select(UserInteraction)
            .where(UserInteraction.provider_reference == provider_reference)
            .order_by(UserInteraction.created_at.desc())
            .limit(1)
        )
        return session.scalars(stmt).first()

    async def _publish_updated(self, provider_reference: str, state: InteractionStatus) -> None:
        """Build one ``UserInteractionUpdated`` and publish it on the bus."""
        event = UserInteractionUpdated(
            session_id=provider_reference,
            state=state.value,
        )
        await self._bus.publish(self._envelope_factory(event))


# --- Command handlers (SFP-43 registry; no Slack I/O — posting is SFP-133) ----
#
# The ``@command_handler`` decorators bind the two async callables into the
# module-level default registry at import time. The service instance they
# delegate to is RUNTIME wiring (bus + session factory + clock are injected by
# the composition root — SFP-132/SFP-135 wiring): ``set_interaction_service``
# is the late-binding setter that root calls at startup.

_bound_service: InteractionService | None = None


def set_interaction_service(service: InteractionService) -> None:
    """Bind the wired :class:`InteractionService` the command handlers use.

    Composition-root responsibility (SFP-132/SFP-135 wiring): construct the
    service with its bus/session/clock seams and bind it once at startup.
    Last-write-wins so tests can rebind per case.
    """
    global _bound_service
    _bound_service = service


def _require_service() -> InteractionService:
    """Return the bound service or raise the pinned not-wired error."""
    if _bound_service is None:
        raise RuntimeError(
            "No InteractionService bound — call set_interaction_service(service) "
            "from the composition root before dispatching commands"
        )
    return _bound_service


@command_handler(RequestUserInput)
async def handle_request_user_input(payload: RequestUserInput, context: MessageContext) -> None:
    """Find-or-create the interaction a ``RequestUserInput`` opens.

    Pure delegation to the bound :class:`InteractionService`: the command's
    ``session_id`` IS the provider thread reference in v0 (one interaction ↔
    one Slack thread, MAS §9.4). The prompt becomes the opening ``question``
    and ``response_required`` is true — the command solicits a user response.
    No Slack I/O here: posting the prompt to the thread is SFP-133
    (``slack_outbound``); the context is accepted per the handler shape
    (SFP-43/44) and unused.
    """
    await _require_service().create(
        payload.session_id,
        interaction_type="user_input_request",
        question=payload.prompt,
        response_required=True,
    )


@command_handler(NotifyUser)
async def handle_notify_user(payload: NotifyUser, context: MessageContext) -> None:
    """Find-or-create the interaction a ``NotifyUser`` rides on.

    Pure delegation to the bound :class:`InteractionService`: the command's
    ``session_id`` IS the provider thread reference in v0. The message becomes
    the opening ``question`` and ``response_required`` is false — the command
    only notifies. No Slack I/O here: posting is SFP-133 (``slack_outbound``);
    the context is accepted per the handler shape (SFP-43/44) and unused.
    """
    await _require_service().create(
        payload.session_id,
        interaction_type="notification",
        question=payload.message,
        response_required=False,
    )
