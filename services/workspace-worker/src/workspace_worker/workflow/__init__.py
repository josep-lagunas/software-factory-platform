"""Workflow logic for the workspace-worker (SFP-75).

This package is owned by SFP-75 and hosts the pure
:func:`~workspace_worker.workflow.failure.classify_failure` function, which maps
a :class:`~sfp_contracts.workflow.failure.FailureSource` (plus optional
``exit_code``/``message``) to a deterministic
:class:`~sfp_contracts.workflow.failure.FailureClassification` per ID-068/ID-069.

Re-exports are deferred until downstream callers (CI runner, git-adapter error
path, Orchestrator workflow engine) exist; import from the full module path
(``workspace_worker.workflow.failure``).
"""
