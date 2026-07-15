"""The :class:`ReviewerOutput` schema — judgment-only, no comments (ID-066).

Grounded in:
- ID-066 — Reviewer returns holistic PR-level judgments; review comments live
  on GitHub, deterministic facts (CI/gate status) are not echoed.
- SFP-33 (Jira) / SFP-16 (doc) — the implementation ticket.

Design choices:
- ``extra='forbid'`` rejects unknown fields (e.g. ``comments[]``) immediately,
  not silently.
- ``review_status`` is a ``StrEnum`` so JSON serialization yields the plain
  string (per ID-013).
- ``quality_gates`` is a dict of booleans — PR-holistic, not per-file.
"""

from enum import StrEnum
from typing import ClassVar

from pydantic import BaseModel, ConfigDict


class ReviewStatus(StrEnum):
    """The four terminal review verdicts the Reviewer can return."""

    APPROVED = "APPROVED"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    BLOCKED = "BLOCKED"
    NEEDS_HUMAN_DECISION = "NEEDS_HUMAN_DECISION"


class QualityGates(BaseModel):
    """Holistic quality gates for the PR — booleans, not per-file."""

    blueprint_compliance: bool
    acceptance_criteria_satisfied: bool
    test_plan_satisfied: bool
    no_unrelated_changes: bool
    maintainability_acceptable: bool
    security_acceptable: bool


class ReviewerOutput(BaseModel):
    """The Reviewer's output schema (judgment-only).

    Fields:
        pr_spec_id: The PR-spec being reviewed.
        review_status: The verdict (one of the four ReviewStatus values).
        quality_gates: Holistic quality gate evaluations (six booleans).

    Constraints (ID-066):
        - NO ``comments[]`` field (comments live on GitHub).
        - NO ``ci_passed`` / ``validation_profile_gates_satisfied``.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    pr_spec_id: str
    review_status: ReviewStatus
    quality_gates: QualityGates

    def to_json(self) -> str:
        """Serialize to a JSON string (delegates to pydantic)."""
        return self.model_dump_json()

    @classmethod
    def from_json(cls, data: str | bytes) -> "ReviewerOutput":
        """Deserialize from a JSON string or bytes."""
        return cls.model_validate_json(data)
