"""The abstract :class:`MessageEnvelope` base — the common envelope every
command and event carries (MAS §4.7 / SFP-219).

Grounded in:
- MAS §4.7 — every inter-agent message (command or event) carries a uniform
  message envelope of routing/dedup metadata plus a payload; commands and events
  share one envelope shape, distinguished by their discriminator and payload.
- ID-013 — JSON is the reference serialization format, which drives the
  ``to_json`` / ``from_json`` serde helpers.
- SFP-219 — the reconciliation ticket: this base unifies the former
  command/event envelopes under one message-routing shape. The concrete
  :class:`~sfp_contracts.commands.envelope.CommandEnvelope` and
  :class:`~sfp_contracts.events.envelope.EventEnvelope` subclass it and add
  their discriminator (``command_type`` / ``event_type``) plus a typed payload.

Design choices:
- The base is payload-agnostic: ``payload`` is typed :class:`typing.Any` to avoid
  an import cycle with the payloads modules (R5). Subclasses narrow it to the
  concrete :class:`~sfp_contracts.commands.payloads.CommandPayload` /
  :class:`~sfp_contracts.events.payloads.EventPayload` hierarchy.
- ``occurred_at`` is a string holding an ISO-8601 timestamp, not
  :class:`datetime.datetime`. Avoiding runtime-only types keeps ``from_json``
  round-trips deterministic and clock-free (matching every sibling contract).
- ``extra='forbid'`` rejects unknown fields, matching every other contract in
  this package. Subclasses inherit it.
"""

from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict


class MessageEnvelope(BaseModel):
    """The common envelope every inter-agent message carries (MAS §4.7 / SFP-219).

    Fields are the uniform message-routing metadata shared by every command and
    event, in declaration order: ``message_id`` (this message's identity),
    ``idempotency_key`` (dedup key), ``correlation_id`` (the causal chain this
    message belongs to), ``causation_id`` (the message that caused this one),
    ``occurred_at`` (when it occurred, ISO-8601 string), and the opaque
    ``payload``. Unknown fields are rejected (``extra='forbid'``), inherited by
    every subclass.

    ``occurred_at`` is a string (ISO-8601), not ``datetime``: avoiding
    runtime-only types keeps ``from_json`` round-trips deterministic and
    clock-free.

    ``payload`` is typed ``Any`` here to avoid an import cycle with the payloads
    modules; the concrete subclasses (:class:`CommandEnvelope`,
    :class:`EventEnvelope`) narrow it to a typed payload base.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    message_id: str
    idempotency_key: str
    correlation_id: str
    causation_id: str
    occurred_at: str
    payload: Any

    def to_json(self) -> str:
        """Serialize this message to a JSON string (delegates to pydantic)."""
        return self.model_dump_json()

    @classmethod
    def from_json(cls, data: str | bytes) -> "MessageEnvelope":
        """Deserialize a message from a JSON string or bytes.

        Calling on a concrete subclass returns an instance of that subclass;
        calling on :class:`MessageEnvelope` returns a generic envelope.
        """
        return cls.model_validate_json(data)
