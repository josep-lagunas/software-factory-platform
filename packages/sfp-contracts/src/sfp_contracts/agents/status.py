"""Terminal status of an agent run.

Members are the authoritative set defined in ID-066 and the SFP-13 ticket.
``AgentStatus`` subclasses :class:`enum.StrEnum` so JSON serialization yields
the plain string value (see ID-013), rather than the ``EnumType.NAME`` form
the default ``Enum`` serializer produces. ``StrEnum`` (Py3.11+) is the
lint-compliant (UP042) successor to ``str, Enum`` and is equivalent for the
"serialize as the string value" requirement.
"""

from enum import StrEnum


class AgentStatus(StrEnum):
    """The five terminal statuses an SFP agent can report."""

    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    NEEDS_HUMAN = "NEEDS_HUMAN"
    NEEDS_RETRY = "NEEDS_RETRY"
