"""The :class:`CommandEnvelope` base and the :class:`CommandType` discriminator.

Grounded in:
- MAS §5.3 — every command carries a message envelope of common metadata plus a
  per-command payload.
- ID-031 — authoritative source for the envelope field set (``message_id``,
  ``idempotency_key``, ``correlation_id``, ``causation_id``, ``occurred_at``)
  and the 8 command names.
- ID-013 — JSON is the reference serialization format, which drives the
  ``to_json`` / ``from_json`` serde helpers.
- ID-072 — issuer ownership: the Orchestrator issues most commands (documented
  per command; not enforced at the schema layer, where there is no ``producer``
  field, since identity is runtime policy).
- SFP-38 — the implementation ticket (Pydantic v2, ``extra='forbid'``).

Design choices:
- This is the COMMAND envelope — distinct from the event envelope's
  ``event_id`` / ``producer`` / ``event_type``. It carries message-routing
  metadata (``message_id``, ``idempotency_key``, ``correlation_id``,
  ``causation_id``, ``occurred_at``) but, unlike events, no ``producer``: the
  issuer identity is runtime policy (ID-072), not schema.
- ``occurred_at`` is a string holding an ISO-8601 timestamp, not
  :class:`datetime.datetime`. The sibling schemas avoid runtime-only types so
  that ``from_json`` round-trips stay deterministic and clock-free; commands
  follow the same rule.
- ``command_type`` is a :class:`enum.StrEnum` so JSON serialization yields the
  plain string value (per ID-013), and is discriminable across the 8 commands.
- Each concrete command fixes its ``command_type`` to exactly one member; the
  inherited ``_enforce_expected_command_type`` validator rejects a mismatched
  value so a producer wiring error surfaces at the contract boundary rather
  than being silently dropped.
- ``extra='forbid'`` rejects unknown fields, matching every other contract in
  this package.
"""

from enum import StrEnum
from typing import ClassVar, Self

from pydantic import BaseModel, ConfigDict, model_validator


class CommandType(StrEnum):
    """The 8 discriminable command types in the SFP command catalogue (ID-031 / MAS §5.3).

    Members are the authoritative set fixed by ID-031, EXCLUDING
    ``GeneratePRSpecifications`` which is an internal Orchestrator operation
    (MAS §5.3), not an inter-agent command. ``StrEnum`` (Py3.11+) is the
    lint-compliant (UP042) successor to ``str, Enum`` and makes JSON
    serialization yield the plain string value (per ID-013).
    """

    EXECUTE_CODING_JOB = "EXECUTE_CODING_JOB"
    SYNCHRONIZE_PULL_REQUEST = "SYNCHRONIZE_PULL_REQUEST"
    CANCEL_CODING_JOB = "CANCEL_CODING_JOB"
    REVIEW_PULL_REQUEST = "REVIEW_PULL_REQUEST"
    CANCEL_REVIEW_JOB = "CANCEL_REVIEW_JOB"
    REQUEST_USER_INPUT = "REQUEST_USER_INPUT"
    NOTIFY_USER = "NOTIFY_USER"
    REQUEST_MERGE = "REQUEST_MERGE"


class CommandEnvelope(BaseModel):
    """The common envelope every command carries (MAS §5.3 / ID-031 / SFP-38).

    Fields are the message-routing envelope: ``message_id`` (this message's
    identity), ``idempotency_key`` (dedup key), ``correlation_id`` (the causal
    chain this command belongs to), ``causation_id`` (the message that caused
    this one) and ``occurred_at`` (when it was issued). Concrete commands (in
    :mod:`sfp_contracts.commands.models`) subclass this base, fix their
    ``command_type`` to one :class:`CommandType` member, and add a typed
    ``payload``. Unknown fields are rejected (``extra='forbid'``).

    Unlike the event envelope, there is no ``producer`` field: the issuer's
    identity is runtime policy (ID-072), not schema.

    ``occurred_at`` is a string (ISO-8601), not ``datetime``: the sibling
    schemas avoid runtime-only types so ``from_json`` round-trips are
    deterministic and clock-free, and commands follow the same rule.

    Subclasses set ``EXPECTED_COMMAND_TYPE`` to their member so the inherited
    validator enforces the exact ``command_type`` value; the base leaves it as
    ``None`` (no-op) and is therefore a valid generic envelope.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    message_id: str
    idempotency_key: str
    correlation_id: str
    causation_id: str
    occurred_at: str
    command_type: CommandType

    #: The exact ``CommandType`` a concrete subclass carries. The base declares
    #: ``None`` so the validator no-ops here and enforces the value on every
    #: subclass that overrides it. Declared as ``ClassVar`` so pydantic treats
    #: it as metadata, not a model field.
    EXPECTED_COMMAND_TYPE: ClassVar[CommandType | None] = None

    @model_validator(mode="after")
    def _enforce_expected_command_type(self) -> Self:
        """Reject a ``command_type`` that does not match this command's member.

        Only active on subclasses that set ``EXPECTED_COMMAND_TYPE``; lets a
        producer wiring error (e.g. an ``ExecuteCodingJob`` carrying
        ``REQUEST_MERGE``) surface at the contract boundary.
        """
        expected = self.EXPECTED_COMMAND_TYPE
        if expected is not None and self.command_type != expected:
            raise ValueError(
                f"command_type must be {expected.value}, got {self.command_type.value}"
            )
        return self

    def to_json(self) -> str:
        """Serialize this command to a JSON string (delegates to pydantic)."""
        return self.model_dump_json()

    @classmethod
    def from_json(cls, data: str | bytes) -> "CommandEnvelope":
        """Deserialize a command from a JSON string or bytes.

        Calling on a concrete subclass returns an instance of that subclass;
        calling on :class:`CommandEnvelope` returns a generic envelope.
        """
        return cls.model_validate_json(data)
