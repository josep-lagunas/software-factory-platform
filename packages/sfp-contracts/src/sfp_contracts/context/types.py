"""The :class:`ContextCatalogue` — a versioned registry of cross-ticket context types.

Grounded in:
- ID-016 — secret outputs are marked, never stored; a ``secret_ref`` marker type
  carries the reference, not the value.
- ID-071 — the catalogue carries a ``schema_version`` so additions don't break
  older tickets.
- SFP-36 — the implementation ticket (Pydantic v2, ``extra='forbid'``).

Design choices (mirroring the sibling schemas in :mod:`sfp_contracts.agents`):
- ``ContextTypeKind`` subclasses :class:`enum.StrEnum` so JSON serialization
  yields the plain string value (see ID-013).
- ``ContextType`` has **no value field** by design — a context type is a *typed
  name*, not a binding. For ``secret_ref`` entries the secret value is never
  stored or carried (ID-016); the entry merely advertises that a secret
  reference of this name exists.
- ``extra='forbid'`` throughout so schema drift surfaces immediately.
"""

from enum import StrEnum
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

#: The schema version of the default context-types catalogue (ID-071).
CURRENT_SCHEMA_VERSION = "1"


class ContextTypeKind(StrEnum):
    """The kind of value a context entry names.

    ``STR`` marks an ordinary string-valued context variable. ``SECRET_REF``
    marks a reference to a secret (ID-016): the entry carries the *name* of the
    secret reference, never the secret value itself.
    """

    STR = "str"
    SECRET_REF = "secret_ref"


class ContextType(BaseModel):
    """A single context-type entry: a name plus its kind marker.

    There is deliberately **no value field** — a context type is a *typed name*,
    not a binding. For ``kind == SECRET_REF`` entries the value is never stored
    or carried (ID-016); the entry merely advertises that a secret reference of
    this name exists. Unknown fields are rejected (``extra='forbid'``).
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    name: str
    kind: ContextTypeKind

    @property
    def is_secret(self) -> bool:
        """Whether this entry marks a secret reference (ID-016)."""
        return self.kind is ContextTypeKind.SECRET_REF


class ContextCatalogue(BaseModel):
    """A versioned registry of cross-ticket context types (ID-071).

    ``schema_version`` lets the catalogue evolve without breaking older tickets:
    a consumer that knows version N can decide how to treat entries from a newer
    catalogue rather than failing silently. ``entries`` lists the typed names;
    secret values themselves are never present (ID-016). Unknown fields are
    rejected (``extra='forbid'``).
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    schema_version: str
    entries: list[ContextType] = Field(default_factory=list)

    def to_json(self) -> str:
        """Serialize this catalogue to a JSON string (delegates to pydantic)."""
        return self.model_dump_json()

    @classmethod
    def from_json(cls, data: str | bytes) -> "ContextCatalogue":
        """Deserialize a :class:`ContextCatalogue` from a JSON string or bytes."""
        return cls.model_validate_json(data)


#: The default context-types catalogue, carrying the canonical cross-ticket
#: entries (ID-016 / ID-071). Add new entries here, bumping
#: :data:`CURRENT_SCHEMA_VERSION` so older tickets keep round-tripping.
DEFAULT_CATALOGUE: ContextCatalogue = ContextCatalogue(
    schema_version=CURRENT_SCHEMA_VERSION,
    entries=[
        ContextType(name="repo_url", kind=ContextTypeKind.STR),
        ContextType(name="db_endpoint", kind=ContextTypeKind.STR),
        ContextType(name="db_secret_arn", kind=ContextTypeKind.SECRET_REF),
        ContextType(name="aws_account_id", kind=ContextTypeKind.STR),
        ContextType(name="llm_provider_secret_ref", kind=ContextTypeKind.SECRET_REF),
    ],
)
