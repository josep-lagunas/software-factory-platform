"""Workflow logic for the workspace-worker (SFP-75 / SFP-74 / SFP-67).

This package hosts the workspace-worker's pure workflow functions:

- :func:`~workspace_worker.workflow.failure.classify_failure` (SFP-75) maps a
  :class:`~sfp_contracts.workflow.failure.FailureSource` (plus optional
  ``exit_code``/``message``) to a deterministic
  :class:`~sfp_contracts.workflow.failure.FailureClassification` per ID-068/ID-069.
- :func:`~workspace_worker.workflow.validation.evaluate_validation_gates`
  (SFP-74) resolves a :class:`~sfp_contracts.validation.profiles.ValidationProfile`
  into a :class:`~sfp_contracts.validation.gate_decision.GateDecision`
  (required gates + human-approval/auto-merge flags) per ID-024/ID-067.
- :func:`~workspace_worker.workflow.readiness_rubric.evaluate_readiness_rubric`
  (SFP-67) rule-checks a parsed ticket for the mandatory ID-070 sections.

Re-exports are deferred until downstream callers (CI runner, git-adapter error
path, Orchestrator workflow engine) exist; import from the full module path
(e.g. ``workspace_worker.workflow.validation``).
"""
