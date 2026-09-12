"""Application layer of the communication service.

- :mod:`communication.application.operational_commands` — the deterministic
  Slack operational-command interpreter (SFP-244): raw ``event.text`` in, a
  typed :class:`OperationalCommand` (or ``None``) out. Parse layer only —
  no execution, no replies, no bus interaction.
- :mod:`communication.application.interaction_service` — the
  :class:`InteractionService` owning the ``UserInteraction`` lifecycle
  (SFP-129): find-or-create + complete transitions, the derived
  timestamp-only status view, ``UserInteractionUpdated`` events on the
  injected MessageBus, and the ``@command_handler`` handlers for
  ``RequestUserInput`` / ``NotifyUser`` that delegate to it (no Slack I/O).
"""

from communication.application.interaction_service import (
    InteractionService,
    InteractionStatus,
    InteractionTransitionError,
    derive_status,
    handle_notify_user,
    handle_request_user_input,
    make_user_interaction_updated_envelope,
    session_scope,
    set_interaction_service,
)
from communication.application.operational_commands import (
    OperationalCommand,
    OperationalCommandInterpreter,
    OperationalCommandKind,
)

__all__ = [
    "InteractionService",
    "InteractionStatus",
    "InteractionTransitionError",
    "OperationalCommand",
    "OperationalCommandKind",
    "OperationalCommandInterpreter",
    "derive_status",
    "handle_notify_user",
    "handle_request_user_input",
    "make_user_interaction_updated_envelope",
    "session_scope",
    "set_interaction_service",
]
