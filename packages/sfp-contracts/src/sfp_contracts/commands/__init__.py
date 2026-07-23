"""Command contracts: the inter-agent command catalogue (MAS §5.3 / MAS §4.7).

This package models the platform's command bus messages. Every command shares a
common :class:`~sfp_contracts.commands.envelope.CommandEnvelope` (itself a
:class:`~sfp_contracts.messages.MessageEnvelope`, carrying ``message_id``,
``idempotency_key``, ``correlation_id``, ``causation_id``, ``occurred_at``) plus
a ``command_type`` discriminator and a per-command typed ``payload``. The 8
concrete commands are the payload classes (MAS §4.7 / SFP-219); their
discriminable ``command_type`` values are fixed by MAS §5.3 / ID-031.
``GeneratePRSpecifications`` is excluded as an internal Orchestrator operation
(MAS §5.3).

Concrete command names and the :class:`CommandPayload` base are re-exported from
:mod:`sfp_contracts.commands.payloads`; the envelope and discriminator are
re-exported from :mod:`sfp_contracts.commands.envelope`.
"""

from sfp_contracts.commands.envelope import CommandEnvelope, CommandType
from sfp_contracts.commands.payloads import (
    CancelCodingJob,
    CancelReviewJob,
    CommandPayload,
    ExecuteCodingJob,
    NotifyUser,
    RequestMerge,
    RequestUserInput,
    ReviewPullRequest,
    SynchronizePullRequest,
)

__all__ = [
    "CancelCodingJob",
    "CancelReviewJob",
    "CommandEnvelope",
    "CommandPayload",
    "CommandType",
    "ExecuteCodingJob",
    "NotifyUser",
    "RequestMerge",
    "RequestUserInput",
    "ReviewPullRequest",
    "SynchronizePullRequest",
]
