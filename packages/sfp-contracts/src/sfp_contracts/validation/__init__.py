"""Validation contracts: risk-tiered profiles and their gate mappings.

The :class:`ValidationProfile` enum is the typed field used by the planner output
(SFP-14), because a PR-spec's ``validation_profile`` must reference a single
canonical enum (ID-067). The profile -> required-gates mapping and the
human-approval rule are pure data landed in SFP-41 (ID-024).

SFP-74 adds the :class:`GateDecision` contract — the pure-data model that
surfaces, for a given profile, which gates the workflow enforces and whether a
human approval is required before merge. The *logic* that populates it
(:func:`workspace_worker.workflow.validation.evaluate_validation_gates`) lives in
the workspace-worker service.
"""

from sfp_contracts.validation.gate_decision import GateDecision

__all__ = ["GateDecision"]
