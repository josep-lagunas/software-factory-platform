"""The readiness schemas — :class:`ParsedTicket` input and :class:`ReadinessOutput`.

Grounded in:
- ID-064 — the Readiness evaluator assesses whether a ticket's context is
  sufficient to plan against, emitting one of three verdicts that drive routing.
- ID-065 — ``verdict`` drives routing: ``READY`` proceeds to the Planner,
  ``NEEDS_CLARIFICATION`` routes back to the user for disambiguation, and
  ``MANUAL_REQUIRED`` escalates to a human.
- ID-071 — ``missing_inputs`` enumerates unresolved context that blocked a
  ``READY`` verdict (the context the Readiness evaluator could not resolve).
- SFP-35 (Jira) — the implementation ticket (Pydantic v2, ``extra='forbid'``).
- SFP-67 (Jira) — :class:`ParsedTicket`, the eight ID-070 sections as the
  rubric's structured input (colocated with :class:`ReadinessOutput`).

Design choices:
- :class:`ParsedTicket` carries the eight ID-070 ticket sections as
  ``str | None`` fields and is colocated with :class:`ReadinessOutput` (precedent:
  :class:`~sfp_contracts.agents.test_designer.TestPlan` + ``TestDesignerOutput``);
  its field names are the fixed ``rubric_results`` keys the rubric emits.
- ``extra='forbid'`` rejects unknown fields immediately rather than silently
  dropping them (shared convention with the sibling agent payloads).
- ``verdict`` is a ``StrEnum`` so JSON serialization yields the plain string
  value (per ID-013), matching :class:`~sfp_contracts.agents.coder.ValidationStatus`
  and :class:`~sfp_contracts.agents.reviewer.ReviewStatus`.
- ``rubric_results`` is a ``dict[str, bool]`` (rubric-check name -> passed). It is
  populated by the **deterministic readiness rubric** (ID-064 layer 1), which
  checks the ticket carries the mandatory ID-070 sections. The dict is keyed by
  section name so the rubric's checks are a fixed, deterministic set (not a
  per-ticket judgment call). The **model evaluator** (ID-064 layer 2) is a
  separate concern that populates ``blocking_ambiguities`` with semantic gaps;
  the two layers compose into one ``ReadinessOutput``.
"""

from enum import StrEnum
from typing import ClassVar

from pydantic import BaseModel, ConfigDict


class ReadinessVerdict(StrEnum):
    """The three routing verdicts the Readiness evaluator can return (ID-065).

    Subclasses :class:`enum.StrEnum` so JSON serialization yields the plain
    string value (see ID-013), matching the sibling verdict enums. ``verdict``
    drives the Orchestrator's routing: ``READY`` proceeds to the Planner,
    ``NEEDS_CLARIFICATION`` routes back to the user, and ``MANUAL_REQUIRED``
    escalates to a human.
    """

    READY = "READY"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    MANUAL_REQUIRED = "MANUAL_REQUIRED"


class ParsedTicket(BaseModel):
    """The eight mandatory ID-070 ticket sections as the rubric's structured input.

    Each field is one section of a parsed ticket, as text. ``None`` (or a
    whitespace-only string, per the rubric) means the section is absent. The
    field names are exactly the eight ID-070 section names and double as the
    fixed ``rubric_results`` keys the deterministic readiness rubric emits
    (ID-064 layer 1) — they are therefore load-bearing and not free to rename.

    Grounded in SFP-67 (Jira); colocated with :class:`ReadinessOutput` so the
    rubric's input and output live together (precedent:
    :class:`~sfp_contracts.agents.test_designer.TestPlan` colocated with
    ``TestDesignerOutput``). Unknown fields are rejected (``extra='forbid'``).

    Fields:
        context: The ticket's context section.
        requirements: The ticket's requirements section.
        files_to_create_modify: The ticket's files-to-create/modify section.
        implementation_notes: The ticket's implementation-notes section.
        references: The ticket's references section.
        context_outputs_required_inputs: The ticket's
            context-outputs/required-inputs section.
        acceptance_criteria: The ticket's acceptance-criteria section.
        dependencies: The ticket's dependencies section.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    context: str | None = None
    requirements: str | None = None
    files_to_create_modify: str | None = None
    implementation_notes: str | None = None
    references: str | None = None
    context_outputs_required_inputs: str | None = None
    acceptance_criteria: str | None = None
    dependencies: str | None = None


class ReadinessOutput(BaseModel):
    """The Readiness evaluator's output schema (the readiness gate).

    Fields:
        ticket_id: The ticket whose readiness was assessed.
        verdict: The routing verdict (one of the three ReadinessVerdict values).
        blocking_ambiguities: Bullet-style ambiguities that block a READY
            verdict (each a short string).
        missing_inputs: Unresolved context the evaluator could not resolve
            (ID-071); bullet-style, each a short string.
        rubric_results: A mapping of rubric-check name -> passed (bool). The
            keys are the fixed ID-070 section names (checked by the
            deterministic readiness rubric, ID-064 layer 1), not a free-form
            or per-ticket shape.

    ``blocking_ambiguities`` and ``missing_inputs`` are required lists that may
    be empty (e.g. a ``READY`` verdict conveys "none blocking"); imposing
    non-emptiness is a workflow policy, not a schema concern, so no
    ``min_length`` is set — matching the per-field convention in
    :class:`~sfp_contracts.agents.coder.CoderOutput`. Unknown fields are
    rejected (``extra='forbid'``).
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    ticket_id: str
    verdict: ReadinessVerdict
    blocking_ambiguities: list[str]
    missing_inputs: list[str]
    rubric_results: dict[str, bool]

    def to_json(self) -> str:
        """Serialize to a JSON string (delegates to pydantic)."""
        return self.model_dump_json()

    @classmethod
    def from_json(cls, data: str | bytes) -> "ReadinessOutput":
        """Deserialize from a JSON string or bytes."""
        return cls.model_validate_json(data)
