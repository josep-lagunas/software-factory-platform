"""The :class:`CoderOutput` payload — the Coder agent's implementation evidence.

Grounded in:
- ID-022 — the Coder implements one PR task and its tests, then submits the PR
  for review; this payload carries the evidence of that work.
- ID-066 — ``coder-output`` payload is ``pr_spec_id, branch_name,
  pull_request_url, files_changed, tests_added_or_updated, validation_status,
  validation_evidence, known_limitations`` with **no code body** (the code lives
  on the GitHub branch/PR; the contract only references it). Every agent emits a
  strict JSON contract, and unknown fields are rejected (``extra='forbid'``).
- SFP-15 / SFP-32 — the implementation ticket (Pydantic v2, ``extra='forbid'``).

This module defines the *typed payload* that sits inside an
:class:`~sfp_contracts.agents.envelope.AgentOutput` envelope's ``payload``
field; it is not itself an envelope.
"""

from enum import StrEnum
from typing import ClassVar

from pydantic import BaseModel, ConfigDict


class ValidationStatus(StrEnum):
    """The four validation states a Coder run can report (ID-066 / SFP-15).

    Subclasses :class:`enum.StrEnum` so JSON serialization yields the plain
    string value (see ID-013), matching :class:`AgentStatus`'s approach.
    ``validation_status`` drives the Orchestrator's rework decision: ``PASSED``
    proceeds to review, ``FAILED`` triggers rework, while ``PENDING`` and
    ``NOT_RUN`` cover the in-progress and skipped cases.
    """

    PASSED = "PASSED"
    FAILED = "FAILED"
    PENDING = "PENDING"
    NOT_RUN = "NOT_RUN"


class CoderOutput(BaseModel):
    """The Coder agent's typed payload: implementation evidence by reference.

    Field names and semantics follow ID-066 / SFP-15. The code itself is **not**
    carried here — it lives on the branch/PR and is referenced via
    ``branch_name`` and ``pull_request_url`` (the "judgments + references, not
    artifacts" rule of ID-066). Unknown fields are rejected
    (``extra='forbid'``) so schema drift between the Coder producer and its
    consumers surfaces immediately rather than being silently dropped.

    List-typed fields hold bullet-style items (each a short path or evidence
    line); they are required but may be empty (e.g. an empty
    ``known_limitations`` conveys "none reported"). Imposing non-emptiness is a
    workflow policy, not a schema concern, so no ``min_length`` is set —
    matching the per-field convention in
    :class:`~sfp_contracts.agents.planner.PrSpec`.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    pr_spec_id: str
    branch_name: str
    pull_request_url: str
    files_changed: list[str]
    tests_added_or_updated: list[str]
    validation_status: ValidationStatus
    validation_evidence: list[str]
    known_limitations: list[str]

    def to_json(self) -> str:
        """Serialize this payload to a JSON string (delegates to pydantic)."""
        return self.model_dump_json()

    @classmethod
    def from_json(cls, data: str | bytes) -> "CoderOutput":
        """Deserialize a :class:`CoderOutput` from a JSON string or bytes."""
        return cls.model_validate_json(data)
