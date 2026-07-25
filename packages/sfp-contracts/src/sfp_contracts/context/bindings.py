"""The :class:`ContextBinding` / :class:`ResolvedContext` schemas — typed context values.

Grounded in:
- ID-071 — the ticket context contract: each ticket declares its outputs/inputs,
  and completed dependencies' outputs are carried forward as typed bindings.
- ID-016 — secret outputs are references only; a ``SECRET_REF`` binding carries
  the reference string (e.g. an ARN / secret ID), never the secret value itself.
- SFP-66 — the implementation ticket (Pydantic v2, ``extra='forbid'``).

Design choices (mirroring the sibling schemas in :mod:`sfp_contracts.context.types`
and :mod:`sfp_contracts.context.declaration`):
- :class:`ContextBinding` carries an actual value (``str | None``): for a
  ``STR`` :class:`ContextType` the value is the ordinary string value; for a
  ``SECRET_REF`` :class:`ContextType` the value is the *reference* string (an ARN
  / secret ID), never the secret itself (ID-016). A ``None`` value marks a
  binding whose value has not yet been materialised.
- :class:`ResolvedContext` is the output of resolving a ticket's declared
  required inputs against the available bindings: ``resolved`` lists the bindings
  that were found (in declaration order), ``missing`` lists the names that were
  not (also in declaration order). Both default to empty lists.
- ``extra='forbid'`` throughout so schema drift surfaces immediately.
"""

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from .types import ContextType


class ContextBinding(BaseModel):
    """A resolved context value: a typed name plus its carried value.

    ``context_type`` is the :class:`~sfp_contracts.context.types.ContextType`
    (name + kind) this binding instantiates; ``name`` is the free-form binding
    name. ``value`` carries the actual string for a ``STR`` context type, or the
    *reference* string for a ``SECRET_REF`` context type (an ARN / secret ID) —
    never the secret value itself (ID-016). A ``None`` value marks a binding
    whose value has not yet been materialised. ``source_ticket`` is the ticket
    that produced this binding. Unknown fields are rejected (``extra='forbid'``).
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    name: str
    context_type: ContextType
    value: str | None
    source_ticket: str


class ResolvedContext(BaseModel):
    """The result of resolving a ticket's declared required inputs (SFP-66).

    ``resolved`` lists the :class:`ContextBinding` instances found among the
    available bindings, in the order their names appeared in the ticket's
    declared ``required_inputs``; ``missing`` lists the names that were not
    found, also in declaration order. Both default to empty lists so a ticket
    with no required inputs resolves cleanly. Unknown fields are rejected
    (``extra='forbid'``).
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    ticket_id: str
    resolved: list[ContextBinding] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
