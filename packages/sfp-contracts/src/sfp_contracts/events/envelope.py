"""The :class:`EventEnvelope` base and the :class:`EventType` discriminator.

Grounded in:
- MAS §5.4 — every event carries an envelope of common metadata plus a
  per-event payload.
- ID-031 — authoritative source for the envelope field set (``event_id``,
  ``occurred_at``, ``producer``, ``event_type``) and the 11 event names.
- ID-013 — JSON is the reference serialization format, which drives the
  ``to_json`` / ``from_json`` serde helpers.
- ID-072 — producer ownership: the Orchestrator produces
  ``TicketUpdated``, ``PRSpecificationsUpdated``, ``DeploymentUpdated`` and
  ``WorkflowUpdated`` (documented per event; not enforced at the schema layer,
  where ``producer`` is a free string, since identity is runtime policy).
- SFP-39 — the implementation ticket (Pydantic v2, ``extra='forbid'``).

Design choices:
- ``occurred_at`` is a string holding an ISO-8601 timestamp, not
  :class:`datetime.datetime`. The sibling schemas avoid runtime-only types so
  that ``from_json`` round-trips stay deterministic and clock-free; events
  follow the same rule.
- ``event_type`` is a :class:`enum.StrEnum` so JSON serialization yields the
  plain string value (per ID-013), and is discriminable across the 11 events.
- Each concrete event fixes its ``event_type`` to exactly one member; the
  inherited ``_enforce_expected_event_type`` validator rejects a mismatched
  value so a producer wiring error surfaces at the contract boundary rather
  than being silently dropped.
- ``extra='forbid'`` rejects unknown fields, matching every other contract in
  this package.
"""

from enum import StrEnum
from typing import ClassVar, Self

from pydantic import BaseModel, ConfigDict, model_validator


class EventType(StrEnum):
    """The 11 discriminable event types on the SFP event bus (ID-031 / MAS §5.4).

    Members are the authoritative set fixed by ID-031. ``StrEnum`` (Py3.11+) is
    the lint-compliant (UP042) successor to ``str, Enum`` and makes JSON
    serialization yield the plain string value (per ID-013).
    """

    EXTERNAL_EVENT_RECEIVED = "EXTERNAL_EVENT_RECEIVED"
    TICKET_UPDATED = "TICKET_UPDATED"
    PR_SPECIFICATIONS_UPDATED = "PR_SPECIFICATIONS_UPDATED"
    CODING_JOB_UPDATED = "CODING_JOB_UPDATED"
    REVIEW_UPDATED = "REVIEW_UPDATED"
    USER_INPUT_RECEIVED = "USER_INPUT_RECEIVED"
    USER_INTERACTION_UPDATED = "USER_INTERACTION_UPDATED"
    USER_QUERY_RECEIVED = "USER_QUERY_RECEIVED"
    MERGE_UPDATED = "MERGE_UPDATED"
    DEPLOYMENT_UPDATED = "DEPLOYMENT_UPDATED"
    WORKFLOW_UPDATED = "WORKFLOW_UPDATED"


class EventEnvelope(BaseModel):
    """The common envelope every event carries (MAS §5.4 / ID-031 / SFP-39).

    Fields are the minimum envelope set: ``event_id``, ``occurred_at``,
    ``producer`` and ``event_type``. Concrete events (in
    :mod:`sfp_contracts.events.models`) subclass this base, fix their
    ``event_type`` to one :class:`EventType` member, and add a typed
    ``payload``. Unknown fields are rejected (``extra='forbid'``).

    ``occurred_at`` is a string (ISO-8601), not ``datetime``: the sibling
    schemas avoid runtime-only types so ``from_json`` round-trips are
    deterministic and clock-free, and events follow the same rule.

    Subclasses set ``EXPECTED_EVENT_TYPE`` to their member so the inherited
    validator enforces the exact ``event_type`` value; the base leaves it as
    ``None`` (no-op) and is therefore a valid generic envelope.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    event_id: str
    occurred_at: str
    producer: str
    event_type: EventType

    #: The exact ``EventType`` a concrete subclass carries. The base declares
    #: ``None`` so the validator no-ops here and enforces the value on every
    #: subclass that overrides it. Declared as ``ClassVar`` so pydantic treats
    #: it as metadata, not a model field.
    EXPECTED_EVENT_TYPE: ClassVar[EventType | None] = None

    @model_validator(mode="after")
    def _enforce_expected_event_type(self) -> Self:
        """Reject a ``event_type`` that does not match this event's member.

        Only active on subclasses that set ``EXPECTED_EVENT_TYPE``; lets a
        producer wiring error (e.g. a ``TicketUpdated`` carrying
        ``MERGE_UPDATED``) surface at the contract boundary.
        """
        expected = self.EXPECTED_EVENT_TYPE
        if expected is not None and self.event_type != expected:
            raise ValueError(f"event_type must be {expected.value}, got {self.event_type.value}")
        return self

    def to_json(self) -> str:
        """Serialize this event to a JSON string (delegates to pydantic)."""
        return self.model_dump_json()

    @classmethod
    def from_json(cls, data: str | bytes) -> "EventEnvelope":
        """Deserialize an event from a JSON string or bytes.

        Calling on a concrete subclass returns an instance of that subclass;
        calling on :class:`EventEnvelope` returns a generic envelope.
        """
        return cls.model_validate_json(data)
