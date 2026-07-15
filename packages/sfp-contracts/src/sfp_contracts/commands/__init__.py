"""Command contracts: the inter-agent command catalogue (MAS §5.3).

This package models the platform's command bus messages. Every command shares a
common :class:`~sfp_contracts.commands.envelope.CommandEnvelope`
(``message_id``, ``idempotency_key``, ``correlation_id``, ``causation_id``,
``occurred_at``) and carries a per-command typed ``payload``. The 8 concrete
commands and their discriminable ``command_type`` values are fixed by
MAS §5.3 / ID-031; ``GeneratePRSpecifications`` is excluded as an internal
Orchestrator operation (MAS §5.3).

Re-exports are deferred until downstream consumers exist (mirroring the events
package), to avoid premature coupling.
"""
