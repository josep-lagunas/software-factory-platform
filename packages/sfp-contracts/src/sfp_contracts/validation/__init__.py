"""Validation contracts: risk-tiered profiles, their gate mappings, and the
PRSpec structural linter.

The :class:`ValidationProfile` enum is the typed field used by the planner output
(SFP-14), because a PR-spec's ``validation_profile`` must reference a single
canonical enum (ID-067). The profile -> required-gates mapping and the
human-approval rule are pure data landed in SFP-41 (ID-024).

SFP-74 adds the :class:`GateDecision` contract — the pure-data model that
surfaces, for a given profile, which gates the workflow enforces and whether a
human approval is required before merge. The *logic* that populates it
(:func:`workspace_worker.workflow.validation.evaluate_validation_gates`) lives in
the workspace-worker service.

ID-021 / SFP-236 promote the PRSpec collect-all structural linter — previously a
stdlib-only script in ``tools/check_prspec.py`` (SFP-193) — into this package as
:func:`validate_prspec` (alias :func:`validate`). The tool stays as a thin CLI
wrapper over the typed, CI-mypy-checked validator.
"""

from sfp_contracts.validation.gate_decision import GateDecision
from sfp_contracts.validation.prspec import validate, validate_prspec

__all__ = ["GateDecision", "validate", "validate_prspec"]
