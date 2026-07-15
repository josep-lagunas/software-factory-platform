"""The :class:`ReadinessOutput` schema — the Readiness evaluator's gate verdict.

Grounded in:
- ID-064 — the Readiness evaluator assesses whether a ticket's context is
  sufficient to plan against, emitting one of three verdicts that drive routing.
- ID-065 — ``verdict`` drives routing: ``READY`` proceeds to the Planner,
  ``NEEDS_CLARIFICATION`` routes back to the user for disambiguation, and
  ``MANUAL_REQUIRED`` escalates to a human.
- ID-071 — ``missing_inputs`` enumerates unresolved context that blocked a
  ``READY`` verdict (the context the Readiness evaluator could not resolve).
- SFP-35 (Jira) — the implementation ticket (Pydantic v2, ``extra='forbid'``).

Design choices:
- ``extra='forbid'`` rejects unknown fields immediately rather than silently
  dropping them (shared convention with the sibling agent payloads).
- ``verdict`` is a ``StrEnum`` so JSON serialization yields the plain string
  value (per ID-013), matching :class:`~sfp_contracts.agents.coder.ValidationStatus`
  and :class:`~sfp_contracts.agents.reviewer.ReviewStatus`.
- ``rubric_results`` is a ``dict[str, bool]`` (rubric-check name -> passed) rather
  than a fixed-shape sub-model. The Readiness evaluator's rubric is a judgment
  call whose checks vary per ticket, so a free-form mapping — homologous to the
  holistic-boolean pattern in the sibling schemas — lets the rubric evolve
  without a schema migration while still surface-checking each gate's pass/fail.
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
            rubric is a per-ticket judgment call, so its keys are free-form
            rather than a fixed shape.

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
