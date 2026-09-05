"""The :class:`ReviewerOutput` schema — judgment-only, no comments (ID-066).

Grounded in:
- ID-066 — Reviewer returns holistic PR-level judgments; review comments live
  on GitHub, deterministic facts (CI/gate status) are not echoed.
- SFP-33 (Jira) / SFP-16 (doc) — the implementation ticket.
- SFP-249 — every verdict carries a non-empty ``rationale``: an empty rationale
  on ANY status is a REVIEWER_MALFUNCTION (infra issue, not a code verdict).

Design choices:
- ``extra='forbid'`` rejects unknown fields (e.g. ``comments[]``) immediately,
  not silently.
- ``review_status`` is a ``StrEnum`` so JSON serialization yields the plain
  string (per ID-013).
- ``quality_gates`` is a dict of booleans — PR-holistic, not per-file.
- ``rationale`` is required and non-empty after strip (a whitespace-only
  rationale is rejected) so a verdict can never be a bare status.
"""

from enum import StrEnum
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, field_validator


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
        rationale: WHY the verdict was reached — a concise, non-empty
            explanation (why approved, or which gates failed). Required on
            EVERY status, including ``APPROVED`` (SFP-249): a verdict without a
            rationale is a reviewer malfunction, not a code verdict.

    Constraints (ID-066):
        - NO ``comments[]`` field (comments live on GitHub).
        - NO ``ci_passed`` / ``validation_profile_gates_satisfied``.

    Constraints (SFP-249):
        - ``rationale`` must be non-empty after strip; whitespace-only is
          rejected at validation time.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    pr_spec_id: str
    review_status: ReviewStatus
    quality_gates: QualityGates
    rationale: str

    @field_validator("rationale")
    @classmethod
    def _rationale_non_empty(cls, value: str) -> str:
        """Reject an empty / whitespace-only rationale (SFP-249).

        A rationale that strips to nothing is a verdict-without-reason —
        classified as REVIEWER_MALFUNCTION by the pipeline guard, never acted
        on as a code verdict.
        """
        if not value.strip():
            raise ValueError("rationale must be a non-empty string after strip")
        return value

    def to_json(self) -> str:
        """Serialize to a JSON string (delegates to pydantic)."""
        return self.model_dump_json()

    @classmethod
    def from_json(cls, data: str | bytes) -> "ReviewerOutput":
        """Deserialize from a JSON string or bytes."""
        return cls.model_validate_json(data)
