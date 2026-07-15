"""Event contracts: the inter-agent / external event catalogue (MAS §5.4).

This package models the platform's event bus messages. Every event shares a
common :class:`~sfp_contracts.events.envelope.EventEnvelope` (``event_id``,
``occurred_at``, ``producer``, ``event_type``) and carries a per-event typed
``payload``. The 11 concrete events and their discriminable ``event_type``
values are fixed by MAS §5.4 / ID-031; producer ownership is fixed by ID-072.

Re-exports are deferred until downstream consumers exist (mirroring the agents
package), to avoid premature coupling.
"""
