"""The transport subpackage: concrete :class:`~sfp_messaging.bus.MessageBus`
implementations (MAS §4.5).

This is a namespace package — it intentionally re-exports nothing. Callers
import a concrete transport directly, e.g.::

    from sfp_messaging.transport.in_memory import InMemoryTransport

Grounded in:
- MAS §4.5 — the Message Bus is the async inter-agent channel; a concrete
  transport implements the interface.
- AP-010 — vendor-neutral seam: the transport SDK lives behind the interface.
- SFP-42 — the ``MessageBus`` Protocol (``runtime_checkable``).
- SFP-46 — the in-memory transport (registry-dispatch test bus).
"""
