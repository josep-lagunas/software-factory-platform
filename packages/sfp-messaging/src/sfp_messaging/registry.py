"""The message-type-keyed handler registry (MAS §4.5 / ID-052 / SFP-43).

A :class:`HandlerRegistry` maps a *concrete message class* (the ``type``
object, e.g. :class:`~sfp_contracts.commands.payloads.ExecuteCodingJob`) to the
async callable that handles it. It is the policy-free storage layer the
declarative decorators (SFP-43) write into: it stores and retrieves callables
keyed by class object only and performs no routing, validation, transformation,
or inspection of message contents (AC2 / ID-052). Lookups are exact-key — a
subclass does not inherit its parent's binding (no MRO walk).

Grounded in:
- MAS §4.5 — the Message Bus dispatches commands/events to handlers.
- ID-052 — the registry is a policy-free, type-keyed lookup table.
- SFP-43 — the implementation ticket.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from sfp_messaging.context import MessageContext

# A handler is an async callable over a (payload, MessageContext) pair: the
# concrete message payload, typed Any so this store stays class-agnostic and
# policy-free (ID-052), plus the per-message context (MAS §4.7 / SFP-44).
type _Handler = Callable[[Any, MessageContext], Awaitable[None]]


class HandlerRegistry:
    """A policy-free, message-type-keyed handler store (MAS §4.5 / ID-052 / SFP-43).

    The registry binds a *concrete message class* (the ``type`` object) to the
    async callable that handles it. Lookups are exact-key: only a class that was
    explicitly registered resolves; a subclass does not inherit its parent's
    binding (no MRO walk). The registry never inspects, transforms, validates,
    or branches on message contents — it stores and retrieves callables keyed by
    class object only (AC2 / ID-052).
    """

    def __init__(self) -> None:
        self._handlers: dict[type, _Handler] = {}

    def register(self, message_type: type, handler: _Handler) -> None:
        """Bind ``handler`` to the concrete ``message_type`` (last-write-wins).

        A later registration under the same ``message_type`` replaces the prior
        binding. The registry stores the callable as given; it never wraps it.

        Args:
            message_type: The concrete message class to key the handler under.
            handler: The async callable that handles messages of that type.

        Raises:
            TypeError: if ``message_type`` is not a class or ``handler`` is not
                callable.
        """
        if not inspect.isclass(message_type):
            raise TypeError(f"message_type must be a class, got {message_type!r}")
        if not callable(handler):
            raise TypeError(f"handler must be callable, got {handler!r}")
        self._handlers[message_type] = handler

    def resolve(self, message_type: type) -> _Handler | None:
        """Return the handler bound to ``message_type``, or ``None`` if unbound.

        Lookup is exact-key: only a class that was explicitly registered
        resolves; a subclass does not inherit its parent's binding (no MRO
        walk). Returns ``None`` (never raises) for an unregistered class so the
        registry stays policy-free (ID-052).

        Args:
            message_type: The concrete message class to look up.

        Returns:
            The bound async callable, or ``None`` if no handler is registered.
        """
        return self._handlers.get(message_type)

    def clear(self) -> None:
        """Remove every binding (test isolation / teardown)."""
        self._handlers.clear()


_default_registry: HandlerRegistry = HandlerRegistry()
"""Module-private default registry the declarative decorators write into."""


def get_default_registry() -> HandlerRegistry:
    """Return the module-level default :class:`HandlerRegistry` (SFP-43)."""
    return _default_registry
