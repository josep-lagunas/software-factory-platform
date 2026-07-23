"""The :class:`CommandEnvelope` base and the :class:`CommandType` discriminator.

Grounded in:
- MAS §4.7 — every command carries a :class:`~sfp_contracts.messages.MessageEnvelope`
  of common metadata plus a per-command payload.
- MAS §5.3 — the command catalogue (the 8 inter-agent command names).
- ID-031 — authoritative source for the envelope field set and the 8 command
  names.
- ID-013 — JSON is the reference serialization format (``to_json``/``from_json``).
- ID-072 — issuer ownership: the Orchestrator issues most commands (documented
  per payload; not enforced at the schema layer, where there is no ``producer``
  field, since identity is runtime policy).
- SFP-219 — reconciliation: ``CommandEnvelope`` now subclasses
  :class:`~sfp_contracts.messages.MessageEnvelope` and carries the
  command-specific ``command_type`` + a typed ``payload`` from the
  :class:`~sfp_contracts.commands.payloads.CommandPayload` hierarchy. The former
  per-message envelope subclasses and the ``command_type``/payload consistency
  validator are gone (consistency is deferred to the serde layer, SFP-45).

Design choices:
- ``command_type`` is a :class:`enum.StrEnum` so JSON serialization yields the
  plain string value (per ID-013), and is discriminable across the 8 commands.
- ``payload`` is typed :data:`~typing.Any` (inherited from the payload-agnostic
  base, R5); at runtime it carries a :class:`CommandPayload`-hierarchy instance.
  Narrowing it to the concrete payload type would require a generic envelope or
  a discriminated union, both out of scope here (deferred to serde, SFP-45).
- ``extra='forbid'`` is inherited from :class:`MessageEnvelope`.
- There is intentionally NO validator tying ``command_type`` to the payload's
  command here: that is a serde-layer concern (SFP-45). The envelope only
  enforces that ``command_type`` is a valid :class:`CommandType` member.
"""

from enum import StrEnum

from sfp_contracts.messages import MessageEnvelope


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


class CommandEnvelope(MessageEnvelope):
    """The command envelope: a :class:`MessageEnvelope` plus a ``command_type``
    discriminator (MAS §4.7 / MAS §5.3 / ID-031 / SFP-219).

    Inherits the uniform routing fields (``message_id``, ``idempotency_key``,
    ``correlation_id``, ``causation_id``, ``occurred_at``) and ``extra='forbid'``
    from :class:`~sfp_contracts.messages.MessageEnvelope`. The ``payload`` is
    typed :data:`~typing.Any` at the envelope (inherited from the base, which
    is payload-agnostic to avoid an import cycle, R5): at runtime it carries an
    instance of the :class:`~sfp_contracts.commands.payloads.CommandPayload`
    hierarchy, whose concrete command names live in
    :mod:`sfp_contracts.commands.payloads`. Reconstructing the concrete payload
    *type* from JSON is a serde-layer concern (SFP-45); ``payload: Any`` keeps
    ``to_json`` lossless and ``from_json`` functional in the meantime.

    There is intentionally no ``producer`` field: the issuer's identity is
    runtime policy (ID-072), not schema.

    There is intentionally NO validator tying ``command_type`` to the payload's
    command — consistency is deferred to the serde layer (SFP-45). The envelope
    only enforces that ``command_type`` is a valid :class:`CommandType` member,
    so a mismatched-but-valid member is accepted here.
    """

    command_type: CommandType

    @classmethod
    def from_json(cls, data: str | bytes) -> "CommandEnvelope":
        """Deserialize a command from a JSON string or bytes.

        Calling on :class:`CommandEnvelope` returns a generic command envelope;
        calling on a subclass returns an instance of that subclass.
        """
        return cls.model_validate_json(data)
