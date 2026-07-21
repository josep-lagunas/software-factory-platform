"""Workflow contracts: the failure-classification taxonomy (ID-068/ID-069).

This package is introduced by SFP-75. It hosts the failure-classification
contract — :class:`~sfp_contracts.workflow.failure.FailureCategory`,
:class:`~sfp_contracts.workflow.failure.BlockedCause`,
:class:`~sfp_contracts.workflow.failure.FailureSource`, and the
:class:`~sfp_contracts.workflow.failure.FailureClassification` model — that
classifies why a stage terminated (a development failure vs a blocked stage) and,
for blocked stages, the cause and whether it is auto-recoverable.

Re-exports are deferred until downstream consumers exist, mirroring the
:mod:`sfp_contracts.agents` and :mod:`sfp_contracts.context` package convention.
Callers import from the full module path
(``sfp_contracts.workflow.failure``).
"""
