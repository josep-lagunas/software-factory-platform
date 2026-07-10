"""The :class:`AgentOutput` envelope — the canonical message every agent emits.

Grounded in:
- ID-013 — JSON is the reference serialization format for platform contracts,
  which drives the ``to_json`` / ``from_json`` serde helpers.
- ID-066 — authoritative source for the field list, the 5-value status enum,
  and the ``extra='forbid'`` rejection requirement.
- SFP-13 — the implementation ticket (Pydantic v2, ``extra='forbid'``).
"""

from datetime import datetime
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from .status import AgentStatus


class AgentOutput(BaseModel):
    """A single agent's output message.

    Fields are in the order fixed by ID-066 / SFP-13; do not reorder without
    updating every consumer. Unknown fields are rejected (``extra='forbid'``)
    so a schema drift between producer and consumer surfaces immediately
    rather than being silently dropped.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    schema_version: str
    agent: str
    ticket_id: str
    timestamp: datetime
    status: AgentStatus
    payload: dict[str, Any]
    human_readable_summary: str

    def to_json(self) -> str:
        """Serialize this envelope to a JSON string (delegates to pydantic)."""
        return self.model_dump_json()

    @classmethod
    def from_json(cls, data: str | bytes) -> "AgentOutput":
        """Deserialize an :class:`AgentOutput` from a JSON string or bytes."""
        return cls.model_validate_json(data)
