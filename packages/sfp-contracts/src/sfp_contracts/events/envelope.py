"""The :class:`EventEnvelope` base and the :class:`EventType` discriminator.

Grounded in:
- MAS §4.7 — every event carries a :class:`~sfp_contracts.messages.MessageEnvelope`
  of common metadata plus a per-event payload.
- MAS §5.4 — the event catalogue (the 11 event-bus event names).
- ID-031 — authoritative source for the envelope field set and the 11 event
  names.
- ID-013 — JSON is the reference serialization format (``to_json``/``from_json``).
- ID-072 — producer ownership: the Orchestrator produces
  ``TicketUpdated``, ``PRSpecificationsUpdated``, ``DeploymentUpdated`` and
  ``WorkflowUpdated`` (documented per payload; not enforced at the schema layer,
  where ``producer`` is a free string, since identity is runtime policy).
- SFP-219 — reconciliation: ``EventEnvelope`` now subclasses
  :class:`~sfp_contracts.messages.MessageEnvelope`. The former ``event_id`` is
  renamed ``message_id`` (inherited from the base) and the envelope GAINS
  ``idempotency_key`` / ``correlation_id`` / ``causation_id`` (inherited). The
  former per-message envelope subclasses and the ``event_type``/payload
  consistency validator are gone (consistency is deferred to the serde layer,
  SFP-45).

Design choices:
- ``event_type`` is a :class:`enum.StrEnum` so JSON serialization yields the
  plain string value (per ID-013), and is discriminable across the 11 events.
- ``payload`` is typed :data:`~typing.Any` (inherited from the payload-agnostic
  base, R5); at runtime it carries an :class:`EventPayload`-hierarchy instance.
  Narrowing it to the concrete payload type would require a generic envelope or
  a discriminated union, both out of scope here (deferred to serde, SFP-45).
- ``extra='forbid'`` is inherited from :class:`MessageEnvelope`.
- There is intentionally NO validator tying ``event_type`` to the payload's
  event here: that is a serde-layer concern (SFP-45). The envelope only
  enforces that ``event_type`` is a valid :class:`EventType` member.
"""

from enum import StrEnum

from sfp_contracts.messages import MessageEnvelope


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


class EventEnvelope(MessageEnvelope):
    """The event envelope: a :class:`MessageEnvelope` plus ``event_type``,
    ``producer`` and a ``payload`` (MAS §4.7 / MAS §5.4 / ID-031 / SFP-219).

    Inherits the uniform routing fields from
    :class:`~sfp_contracts.messages.MessageEnvelope` — ``message_id`` (the former
    ``event_id``, renamed SFP-219), ``idempotency_key``, ``correlation_id``,
    ``causation_id``, ``occurred_at`` — and ``extra='forbid'``. The ``payload``
    is typed :data:`~typing.Any` at the envelope (inherited from the
    payload-agnostic base, R5): at runtime it carries an instance of the
    :class:`~sfp_contracts.events.payloads.EventPayload` hierarchy, whose
    concrete event names live in :mod:`sfp_contracts.events.payloads`.
    Reconstructing the concrete payload *type* from JSON is a serde-layer
    concern (SFP-45); ``payload: Any`` keeps ``to_json`` lossless and
    ``from_json`` functional in the meantime.

    ``producer`` is a free string: identity is runtime policy (ID-072), not
    schema.

    There is intentionally NO validator tying ``event_type`` to the payload's
    event — consistency is deferred to the serde layer (SFP-45). The envelope
    only enforces that ``event_type`` is a valid :class:`EventType` member, so a
    mismatched-but-valid member is accepted here.
    """

    event_type: EventType
    producer: str

    @classmethod
    def from_json(cls, data: str | bytes) -> "EventEnvelope":
        """Deserialize an event from a JSON string or bytes.

        Calling on :class:`EventEnvelope` returns a generic event envelope;
        calling on a subclass returns an instance of that subclass.
        """
        return cls.model_validate_json(data)
