"""The :class:`TicketContextDeclaration` schema — a ticket's context I/O contract.

Grounded in:
- ID-070 — the AI-Implementation-Specification ticket template carries the
  ``context_outputs`` / ``required_inputs`` fields this schema backs.
- ID-071 — output type names are validated against the versioned
  :data:`~sfp_contracts.context.types.DEFAULT_CATALOGUE` entry names, so a
  catalogue rename/removal surfaces at the contract boundary rather than
  letting a ticket advertise a value no consumer can resolve.
- SFP-37 — the implementation ticket (Pydantic v2, ``extra='forbid'``).

Design choices:
- Each output carries a ``type`` naming one :data:`DEFAULT_CATALOGUE` entry; a
  baked-in ``@model_validator(mode='after')`` rejects any ``type`` not in the
  catalogue so a typo or stale name cannot advertise a context value that no
  consumer can resolve.
- ``required_inputs`` are deliberately free-form: cross-ticket input
  satisfaction (``source_ticket`` existence/resolution) is the Readiness Gate's
  runtime job, not this schema's, so neither ``name`` nor ``source_ticket`` is
  catalogue-checked here.
- ``extra='forbid'`` throughout so schema drift surfaces immediately, matching
  every other contract in this package (see
  :mod:`sfp_contracts.events.envelope`).
- :meth:`to_json`/:meth:`from_json` live only on
  :class:`TicketContextDeclaration`, mirroring
  :class:`~sfp_contracts.context.types.ContextCatalogue`.
"""

from typing import ClassVar, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .types import DEFAULT_CATALOGUE

#: The set of context-type names a declaration may advertise as outputs,
#: derived once from :data:`DEFAULT_CATALOGUE` (ID-071). A future SFP-36
#: catalogue *addition* is a safe superset; a rename/removal would break
#: validation here, which is the intended coupling (see module R1).
_VALID_OUTPUT_TYPES: frozenset[str] = frozenset(entry.name for entry in DEFAULT_CATALOGUE.entries)


class ContextOutput(BaseModel):
    """A context value this ticket produces (``type`` names a catalogue entry).

    ``type`` is validated against :data:`DEFAULT_CATALOGUE` by
    :class:`TicketContextDeclaration`'s validator; ``name`` is the free-form
    binding name the ticket advertises under that type. Unknown fields are
    rejected (``extra='forbid'``).
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    name: str
    type: str


class ContextInput(BaseModel):
    """A context value this ticket requires from another ticket's outputs.

    Neither ``name`` nor ``source_ticket`` is catalogue-checked: cross-ticket
    input satisfaction (whether ``source_ticket`` exists and actually outputs
    ``name``) is the Readiness Gate's runtime job, not this schema's. Unknown
    fields are rejected (``extra='forbid'``).
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    name: str
    source_ticket: str


class TicketContextDeclaration(BaseModel):
    """A ticket's declarative context I/O contract (ID-070).

    ``outputs`` lists the context values the ticket produces (each ``type``
    validated against :data:`DEFAULT_CATALOGUE`, ID-071); ``required_inputs``
    lists the context values it needs from other tickets (free-form). Both
    default to empty lists so a ticket with no context I/O is valid. Unknown
    fields are rejected (``extra='forbid'``).
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    outputs: list[ContextOutput] = Field(default_factory=list)
    required_inputs: list[ContextInput] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_output_types(self) -> Self:
        """Reject any :class:`ContextOutput` whose ``type`` is not in the catalogue.

        Surfaces a typo or stale type name at the contract boundary rather than
        letting a ticket advertise a context value no consumer can resolve. The
        valid set is :data:`_VALID_OUTPUT_TYPES`, derived from
        :data:`DEFAULT_CATALOGUE` (ID-071).
        """
        for output in self.outputs:
            if output.type not in _VALID_OUTPUT_TYPES:
                raise ValueError(
                    f"unknown context output type {output.type!r}; "
                    f"valid types: {sorted(_VALID_OUTPUT_TYPES)}"
                )
        return self

    def to_json(self) -> str:
        """Serialize this declaration to a JSON string (delegates to pydantic)."""
        return self.model_dump_json()

    @classmethod
    def from_json(cls, data: str | bytes) -> "TicketContextDeclaration":
        """Deserialize a :class:`TicketContextDeclaration` from JSON string/bytes."""
        return cls.model_validate_json(data)
