"""The deterministic validation gate evaluator (SFP-74; ID-024/ID-067).

This module is the *logic* half of SFP-74 — the pure
:func:`evaluate_validation_gates` function that resolves a
:class:`~sfp_contracts.validation.profiles.ValidationProfile` into a fully-populated
:class:`~sfp_contracts.validation.gate_decision.GateDecision` (the required gate
names, the human-approval flag, and the auto-merge eligibility). It depends on
the contracts in :mod:`sfp_contracts.validation.profiles` and
:mod:`sfp_contracts.validation.gate_decision` (the workspace-worker declares
``sfp-contracts`` as a dependency).

Scope note (SFP-74): this ticket delivers **gate enforcement** only. Profile
*assignment* (which profile a given PR-spec carries) remains Planner-owned
(ID-067); this function takes the already-assigned profile as its sole input.

Grounded in:
- ID-024 — the profile -> gates mapping (which gates each tier enforces) and the
  human-approval rule (LEVEL_1 is auto-merge eligible; LEVEL_2/3/4 require a
  human approval before merge). The mapping itself is canonical data in
  :mod:`sfp_contracts.validation.profiles`; this function only *delegates* to it.
- ID-067 — the Planner assigns exactly one profile per PR-spec; that profile is
  the sole input here.

Design choice (mirrors :mod:`workspace_worker.workflow.failure`): this module
holds **no mapping data of its own** — it delegates entirely to the landed
:data:`~sfp_contracts.validation.profiles.GATE_MAPPING` and
:func:`~sfp_contracts.validation.profiles.requires_human_approval`. The function
is a pure resolver: same input always yields an equal
:class:`~sfp_contracts.validation.gate_decision.GateDecision`, and ``auto_merge_eligible``
is derived as the logical complement of ``requires_human_approval`` so the two
are always consistent.
"""

from sfp_contracts.validation.gate_decision import GateDecision
from sfp_contracts.validation.profiles import (
    GATE_MAPPING,
    ValidationProfile,
    requires_human_approval,
)


def evaluate_validation_gates(profile: ValidationProfile) -> GateDecision:
    """Resolve a validation profile into a :class:`GateDecision`.

    Delegates entirely to the canonical landed data in
    :mod:`sfp_contracts.validation.profiles`:

    - ``required_gates`` <- ``list(GATE_MAPPING[profile])`` (ID-024).
    - ``requires_human_approval`` <- :func:`requires_human_approval` ``(profile)``
      (False for LEVEL_1, True for LEVEL_2/3/4; ID-024).
    - ``auto_merge_eligible`` <- ``not requires_human_approval(profile)`` (so
      LEVEL_1 is auto-merge eligible once green; every other tier is not).

    The function is pure and deterministic: no I/O, and the same ``profile``
    always yields an equal :class:`GateDecision`. It introduces no mapping of its
    own; if a gate tier is wrong, the fix is in
    :data:`~sfp_contracts.validation.profiles.GATE_MAPPING`, not here.

    Args:
        profile: The :class:`ValidationProfile` assigned to the PR-spec (ID-067).

    Returns:
        The fully-populated :class:`GateDecision`.
    """
    needs_human = requires_human_approval(profile)
    return GateDecision(
        profile=profile,
        required_gates=list(GATE_MAPPING[profile]),
        requires_human_approval=needs_human,
        auto_merge_eligible=not needs_human,
    )
