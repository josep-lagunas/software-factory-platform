"""The validation gate-decision contract (SFP-74; ID-024/ID-067).

This module is the *contract* half of SFP-74 — the pure
:class:`GateDecision` pydantic model. The *logic* that populates it
(:func:`workspace_worker.workflow.validation.evaluate_validation_gates`) lives in
the workspace-worker service and consumes these types.

Scope note (SFP-74): this ticket delivers **gate enforcement** only — it surfaces,
on a pure-data model, which gates a profile requires and whether a human approval
is needed before merge. Profile *assignment* (which profile a given PR-spec
carries) remains Planner-owned (ID-067) and is out of scope here.

Grounded in:
- ID-024 — the profile -> gates mapping and the human-approval rule: every tier
  enforces the four always-on automated gates; LEVEL_1 is auto-merge eligible
  (no human approval); LEVEL_2/3/4 require a human approval before merge.
- ID-067 — the Planner assigns exactly one :class:`ValidationProfile` to every
  PR-spec; the chosen profile is the sole input to gate enforcement.
- ID-013 — ``StrEnum`` with ``value == name`` so JSON serialization yields the
  plain string member name (carried here via :class:`ValidationProfile`).

Design choices (mirroring the sibling schema
:class:`sfp_contracts.workflow.failure.FailureClassification`):
- :class:`GateDecision` uses ``extra='forbid'`` so schema drift surfaces
  immediately.
- :meth:`GateDecision.to_json` / :meth:`GateDecision.from_json` delegate to
  pydantic, mirroring the failure-classification contract.
- ``auto_merge_eligible`` is carried as stored data even though it is derivable
  as ``not requires_human_approval``; the logic-half function always sets the two
  consistently, so a correctly-constructed instance carries a self-consistent
  pair (see R2).
"""

from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from .profiles import ValidationProfile


class GateDecision(BaseModel):
    """The resolved gate-enforcement decision for a validation profile.

    A pure-data snapshot of *which* gates the workflow must enforce for a given
    :class:`ValidationProfile` and *whether* a human approval is required before
    merge. It carries no behaviour; acting on it (running the gates, gating the
    merge) is the Orchestrator's responsibility, not this model's.

    Fields:
        profile: The :class:`ValidationProfile` this decision was resolved from
            (the sole input to gate enforcement; ID-067).
        required_gates: The ordered list of gate-name strings the workflow
            enforces for ``profile``, as declared by
            :data:`sfp_contracts.validation.profiles.GATE_MAPPING` (ID-024).
        requires_human_approval: Whether ``profile`` requires a human approval
            before merge. ``False`` for :attr:`ValidationProfile.LEVEL_1_INTERNAL`
            only; ``True`` for every other tier (ID-024).
        auto_merge_eligible: Whether the PR is auto-merge eligible once all gates
            are green. The logical complement of ``requires_human_approval``
            (``auto_merge_eligible == not requires_human_approval``); stored
            explicitly so callers need not re-derive it.

    Unknown fields are rejected (``extra='forbid'``), mirroring the sibling agent
    and workflow schemas.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    profile: ValidationProfile
    required_gates: list[str]
    requires_human_approval: bool
    auto_merge_eligible: bool

    def to_json(self) -> str:
        """Serialize this decision to a JSON string (delegates to pydantic)."""
        return self.model_dump_json()

    @classmethod
    def from_json(cls, data: str | bytes) -> "GateDecision":
        """Deserialize a :class:`GateDecision` from a JSON string or bytes."""
        return cls.model_validate_json(data)
