"""Declarative handler decorators (MAS §4.5 / ID-052 / SFP-43).

``@command_handler`` / ``@event_handler`` register an async callable into the
module-level default :class:`~sfp_messaging.registry.HandlerRegistry`, keyed by
the *concrete message class* (e.g.
:class:`~sfp_contracts.commands.payloads.ExecuteCodingJob`), and return the
callable UNCHANGED — no wrapping, no closure, no behaviour (AC2 / ID-052).
Accept a concrete message class (``type``), NOT the
:class:`~sfp_contracts.commands.envelope.CommandType` enum shorthand.

Grounded in:
- MAS §4.5 — the Message Bus dispatches commands/events to handlers.
- ID-052 / AC2 — the decorator's ONLY effect is registration; it performs no
  business logic.
- SFP-43 — the implementation ticket.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from sfp_messaging.context import MessageContext
from sfp_messaging.registry import get_default_registry

# A handler is an async callable over a (payload, MessageContext) pair: the
# concrete message payload, typed Any so registration stays class-agnostic and
# policy-free (ID-052), plus the per-message context (MAS §4.7 / SFP-44).
type _Handler = Callable[[Any, MessageContext], Awaitable[None]]

# A decorator over a handler returns the handler unchanged (AC2).
type _HandlerDecorator = Callable[[_Handler], _Handler]


def command_handler(message_type: type) -> _HandlerDecorator:
    """Return a decorator that registers ``func`` for ``message_type``.

    The returned decorator binds ``func`` in the default registry under the
    concrete ``message_type`` (e.g.
    :class:`~sfp_contracts.commands.payloads.ExecuteCodingJob`) and returns
    ``func`` UNCHANGED — object identity is preserved
    (``command_handler(T)(f) is f``) so the decorator performs no business logic
    (AC2 / ID-052). Accept a concrete command class, NOT the
    :class:`~sfp_contracts.commands.envelope.CommandType` enum shorthand.

    Args:
        message_type: The concrete command class to key the handler under.

    Returns:
        A decorator that registers its argument and returns it unchanged.
    """

    def decorator(func: _Handler) -> _Handler:
        get_default_registry().register(message_type, func)
        return func

    return decorator


def event_handler(message_type: type) -> _HandlerDecorator:
    """Return a decorator that registers ``func`` for ``message_type``.

    Identical in shape to :func:`command_handler`, but documents intent for
    concrete event classes (e.g.
    :class:`~sfp_contracts.events.payloads.UserInputReceived`). Returns ``func``
    UNCHANGED (``event_handler(T)(f) is f``) — no business logic (AC2 / ID-052).

    Args:
        message_type: The concrete event class to key the handler under.

    Returns:
        A decorator that registers its argument and returns it unchanged.
    """

    def decorator(func: _Handler) -> _Handler:
        get_default_registry().register(message_type, func)
        return func

    return decorator
