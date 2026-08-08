"""The :class:`PlannerOutput` payload — the Planner agent's deterministic output.

Grounded in:
- ID-021 — Planner output is deterministic JSON composed of small, self-contained
  pull-request tasks (the "what to build" front-loading).
- ID-066 — ``planner-output`` payload is ``pr_specs[]``; every agent emits a
  strict JSON contract, and unknown fields are rejected (``extra='forbid'``).
- ID-067 — each PR-spec carries a ``validation_profile`` (the
  :class:`~sfp_contracts.validation.profiles.ValidationProfile` enum) that
  selects its risk-tiered gate set.
- SFP-14 — the implementation ticket (Pydantic v2, ``extra='forbid'``,
  ``pr_specs`` non-empty).

This module defines the *typed payload* that sits inside an
:class:`~sfp_contracts.agents.envelope.AgentOutput` envelope's ``payload``
field; it is not itself an envelope.
"""

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from ..validation.profiles import ValidationProfile


class DeferredForeignKeyObligation(BaseModel):
    """A declared-but-deferred intra-service foreign key (ID-058 deferral protocol).

    When an intra-service cross-aggregate reference cannot be created as a real
    ``FOREIGN KEY`` because the target table does not yet exist (the target
    aggregate lands in a separate, later ticket), the originating ticket
    *declares* the obligation here instead of silently shipping a plain column.
    The deferral is also tracked on the target ticket ("close on landing":
    creating the target table MUST add the deferred FK). A deferral is never
    silent — a reference is either a real FK or a declared, tracked obligation.

    Promotes the policy mis-applied in SFP-101 (``Ticket.project_id`` shipped
    no-FK, framed as "§6.5 policy") into a normative, recorded rule: ID-058
    requires intra-service cross-aggregate FKs (MAS §2854-2855, precedent
    ``UserExternalIdentity.user_id``), and this model is the mechanism by which
    a *temporary* omission is declared and tracked rather than dropped.

    Fields are non-empty strings; unknown fields are rejected
    (``extra='forbid'``) so shape drift between the Planner producer and the
    structural linter (``tools/check_prspec.py``) surfaces immediately rather
    than being silently dropped.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    column: str
    target_aggregate: str
    blocked_on: str
    follow_up: str


class PrSpec(BaseModel):
    """A single self-contained pull-request task produced by the Planner.

    Field names and semantics follow ID-021 / ID-066 / SFP-14. Unknown fields are
    rejected (``extra='forbid'``) so schema drift between the Planner producer
    and its consumers surfaces immediately rather than being silently dropped.

    List-typed fields hold bullet-style items (each a short string); the prose
    fields (``goal``, ``validation_profile_reason``, ``implementation_notes``)
    hold free text.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    id: str
    title: str
    goal: str
    scope: list[str]
    out_of_scope: list[str]
    acceptance_criteria: list[str]
    dependencies: list[str]
    validation_profile: ValidationProfile
    validation_profile_reason: str
    required_gates: list[str]
    likely_files_or_modules: list[str]
    risks: list[str]
    implementation_notes: str
    deferred_fk_obligations: list[DeferredForeignKeyObligation] = Field(default_factory=list)


class PlannerOutput(BaseModel):
    """The Planner agent's typed payload: one or more PR-specs.

    ``pr_specs`` must be non-empty (``min_length=1``) — a Planner run that
    produces zero PR-specs is meaningless and must be rejected at the contract
    boundary rather than downstream. Unknown fields are rejected
    (``extra='forbid'``).
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    pr_specs: list[PrSpec] = Field(..., min_length=1)

    def to_json(self) -> str:
        """Serialize this payload to a JSON string (delegates to pydantic)."""
        return self.model_dump_json()

    @classmethod
    def from_json(cls, data: str | bytes) -> "PlannerOutput":
        """Deserialize a :class:`PlannerOutput` from a JSON string or bytes."""
        return cls.model_validate_json(data)
