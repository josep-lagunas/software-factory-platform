"""The vendor-neutral, async-first :class:`MessageBus` interface (SFP-42).

This module is the transport-agnostic seam named by MAS §4.5 (Message Bus): it
defines *what* the bus does — asynchronously publish a command or event and
register an async handler for messages — without naming or importing any
transport SDK. A concrete transport (the in-memory bus SFP-46; the production
transport SFP-101) implements this interface, and the handler-decorator layer
(SFP-43) sits above it.

Grounded in:
- MAS §4.5 — the Message Bus is the async inter-agent channel.
- MAS §5.3 / §5.4 — commands and events carry the typed envelopes from
  SFP-38 / SFP-39.
- AP-010 / MAS §9.6 — vendor-neutral seam design.
- SFP-42 — the implementation ticket.

Design choices:
- :class:`typing.Protocol` (``runtime_checkable``) is preferred over
  :class:`abc.ABC`, matching the agent-runtime seam fixed by SFP-51: a concrete
  transport satisfies the interface structurally (duck typing) without
  inheriting from it, and callers may ``isinstance``-check against the
  abstraction.
- :class:`~sfp_contracts.commands.envelope.CommandEnvelope` and
  :class:`~sfp_contracts.events.envelope.EventEnvelope` are imported for typing
  ONLY (under ``if TYPE_CHECKING:``): this seam declares no runtime dependency
  on ``sfp-contracts`` — the envelopes are the message *shape*, not a runtime
  coupling at this layer.
- ``publish`` / ``subscribe`` are declared ``async``: the bus is async-first
  (MAS §4.5). Routing, fan-out, retry, and idempotency are runtime policy of a
  concrete transport (SFP-46 / SFP-101), not of this interface.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from sfp_contracts.commands.envelope import CommandEnvelope
    from sfp_contracts.events.envelope import EventEnvelope


@runtime_checkable
class MessageBus(Protocol):
    """The async, transport-agnostic message bus (MAS §4.5 / SFP-42).

    A concrete transport (in-memory SFP-46, production SFP-101) implements this
    interface; callers above the seam depend only on ``publish`` /
    ``subscribe``, never on a transport SDK.
    """

    async def publish(self, message: CommandEnvelope | EventEnvelope) -> None:
        """Asynchronously publish a command or event to the bus (MAS §4.5)."""
        ...  # pragma: no cover

    async def subscribe(
        self, handler: Callable[[CommandEnvelope | EventEnvelope], Awaitable[None]]
    ) -> None:
        """Register an async ``handler`` invoked for each published message."""
        ...  # pragma: no cover
