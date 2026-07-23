"""The in-memory :class:`~sfp_messaging.bus.MessageBus` transport (SFP-46).

A concrete transport that satisfies the
:class:`~sfp_messaging.bus.MessageBus` Protocol structurally (duck typing, no
inheritance — SFP-42). It is the registry-dispatching test bus: ``publish``
resolves the handler for the message's payload type from the module-level
default registry (SFP-43) and dispatches the *payload* (not the envelope) to it
inside a bound :class:`~sfp_messaging.context.MessageContext` (MAS §4.7). Every
published message is recorded on ``published_messages`` in publish order so
assertions can inspect what was sent.

Grounded in:
- MAS §4.5 — the Message Bus dispatches commands/events to handlers.
- MAS §4.7 — the envelope dissolves into a ``MessageContext``; the handler
  receives ``(payload, context)``, never the raw envelope.
- Impl Notes §1 — the framework maps message types to handlers via the
  type-keyed registry; the developer never manually subscribes.
- SFP-42 — the ``MessageBus`` Protocol (``runtime_checkable``).
- SFP-43 — the declarative handler decorators + default registry.
- SFP-44 — ``MessageContext`` + ``bind_message_context``.

Design choices:
- ``CommandEnvelope`` / ``EventEnvelope`` are imported for typing ONLY (under
  ``if TYPE_CHECKING:``): this transport declares no runtime dependency on
  ``sfp-contracts`` — the envelopes are the message *shape*, not a runtime
  coupling (mirrors ``bus.py``).
- Runtime imports are limited to ``sfp_messaging.context`` and
  ``sfp_messaging.registry`` — intra-package, no import cycle (``context.py``
  imports neither this module nor the registry).
- ``publish`` records the message BEFORE dispatching (record-before): if the
  handler raises, the message is still recorded (R2).
- ``subscribe`` is intentionally unimplemented: this transport routes ONLY
  through the type-keyed registry (the raw-envelope ``subscribe`` signature of
  the Protocol is satisfied structurally but not exercised — R3). It exists
  solely so the class passes the ``runtime_checkable`` ``isinstance`` test.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sfp_messaging.context import (
    MessageContext,
    _build_message_context,
    bind_message_context,
)
from sfp_messaging.registry import get_default_registry

if TYPE_CHECKING:
    from sfp_contracts.commands.envelope import CommandEnvelope
    from sfp_contracts.events.envelope import EventEnvelope

# A published message is either a command or event envelope. Typed for clarity
# only; at runtime the transport never branches on the envelope type — it
# dispatches on ``type(message.payload)`` (the registry key, ID-052).
type _Message = CommandEnvelope | EventEnvelope


class InMemoryTransport:
    """A registry-dispatching, in-memory :class:`MessageBus` (SFP-46).

    A test/development transport that records every published message and
    dispatches each to the handler registered for its payload type via the
    module-level default :class:`~sfp_messaging.registry.HandlerRegistry`
    (SFP-43). The envelope's routing metadata is dissolved into a
    :class:`~sfp_messaging.context.MessageContext` bound around the handler
    invocation (MAS §4.7); the handler receives ``(payload, context)``.

    Published messages are appended to :attr:`published_messages` in publish
    order, BEFORE dispatch, so the record survives a handler that raises.

    This class satisfies the ``runtime_checkable``
    :class:`~sfp_messaging.bus.MessageBus` Protocol structurally (duck typing,
    no inheritance — SFP-42). It is NOT a production transport (SFP-101): it
    performs no fan-out, retry, idempotency, or durability.
    """

    def __init__(self) -> None:
        #: Every message passed to :meth:`publish`, in publish order. Use this
        #: to assert what was published (e.g. by SFP-32's ``assert_published``).
        #: Identity-preserving: the exact envelope objects are stored.
        self.published_messages: list[_Message] = []

    async def publish(self, message: _Message) -> None:
        """Record ``message`` and dispatch its payload to the registered handler.

        The message is appended to :attr:`published_messages` FIRST (record-
        before), then the handler for ``type(message.payload)`` is resolved from
        the default registry. If a handler is registered, its routing metadata is
        dissolved into a :class:`MessageContext` (MAS §4.7), bound around the
        call, and the handler is awaited as ``handler(payload, context)``. The
        handler's exception (if any) propagates unchanged — it is NOT swallowed.

        If no handler is registered for the payload type, the message stays
        recorded and no dispatch occurs (record + no dispatch, no raise) — this
        is the registry's policy-free ``None`` return (ID-052), not an error.

        Args:
            message: The command or event envelope to publish.
        """
        # Record BEFORE dispatch so the entry survives a handler that raises.
        self.published_messages.append(message)

        # Resolve the handler keyed by the concrete payload type (ID-052).
        handler = get_default_registry().resolve(type(message.payload))
        if handler is None:
            # Unregistered payload type: record + no dispatch, no raise.
            return None

        # Dissolve the envelope's routing metadata into a MessageContext
        # (MAS §4.7): received_at <- envelope.occurred_at, retry_count pinned
        # to 0 (in-memory transport does not retry; no retry field on envelope).
        ctx: MessageContext = _build_message_context(
            correlation_id=message.correlation_id,
            causation_id=message.causation_id,
            message_id=message.message_id,
            received_at=message.occurred_at,
            retry_count=0,
        )

        # Bind the context around the handler invocation so handler code (and
        # anything it calls) can read it via get_current_context(). The handler
        # receives (payload, context) — never the envelope.
        #
        # ``bind_message_context`` is a ``@contextlib.contextmanager`` (a sync
        # context manager); ``async with`` does NOT support it (raises
        # ``TypeError``). The sync ``with`` is semantically identical here: the
        # ContextVar is set before the ``await`` and reset after, and no extra
        # task is spawned so the bound value is visible to the awaited handler.
        with bind_message_context(ctx):
            await handler(message.payload, ctx)

    async def subscribe(self, handler: Any) -> None:  # pragma: no cover
        """Register a raw-envelope handler — intentionally unimplemented.

        This transport routes ONLY through the type-keyed registry: register
        handlers with ``@command_handler`` / ``@event_handler`` (SFP-43), and
        :meth:`publish` dispatches through ``get_default_registry()``.

        This method exists solely to structurally satisfy the
        ``runtime_checkable`` :class:`~sfp_messaging.bus.MessageBus` Protocol;
        it is never exercised (covered via the ``NotImplementedError`` test).

        Raises:
            NotImplementedError: always.
        """
        raise NotImplementedError(
            "InMemoryTransport is a registry-dispatch test transport; "
            "subscribe() is intentionally unimplemented — register handlers "
            "via @command_handler / @event_handler (SFP-43); publish() "
            "dispatches through get_default_registry()"
        )
