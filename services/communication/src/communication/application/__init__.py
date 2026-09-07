"""Application layer of the communication service.

- :mod:`communication.application.operational_commands` — the deterministic
  Slack operational-command interpreter (SFP-244): raw ``event.text`` in, a
  typed :class:`OperationalCommand` (or ``None``) out. Parse layer only —
  no execution, no replies, no bus interaction.
"""

from communication.application.operational_commands import (
    OperationalCommand,
    OperationalCommandInterpreter,
    OperationalCommandKind,
)

__all__ = [
    "OperationalCommand",
    "OperationalCommandKind",
    "OperationalCommandInterpreter",
]
