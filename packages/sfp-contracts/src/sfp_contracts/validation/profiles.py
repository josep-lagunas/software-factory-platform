"""Risk-tiered validation profiles and their gate / approval mapping.

``ValidationProfile`` is the four-level risk tier the Planner assigns to every
PR-spec (ID-067). The chosen profile selects *which* validation gates the
workflow enforces and *whether* a human approval is required before merge.

This module lands in two steps:

* SFP-33 (Jira) shipped the :class:`ValidationProfile` enum ahead of the rest
  of the contract because :class:`~sfp_contracts.agents.planner.PrSpec` needs
  it as a typed field today; ID-067 fully determines the member names.
* **SFP-41** (this change) adds the profile -> gates mapping and the
  human-approval rule as **data**, so the mapping can evolve without redeploying
  workflow code.

Gate names (ID-024 / ID-067)
----------------------------
Gate names are plain strings, deliberately decoupled from any one schema so
the mapping stays pure data. The common subset overlaps the six holistic
booleans on :class:`~sfp_contracts.agents.reviewer.QualityGates`
(``blueprint_compliance``, ``acceptance_criteria_satisfied``,
``test_plan_satisfied``, ``no_unrelated_changes``, ``maintainability_acceptable``,
``security_acceptable``); higher tiers layer dedicated gates that are *not*
part of that holistic six — ``security_review`` (a separate, dedicated security
review) and ``migration_reversibility`` (a reversibility/rollback check for the
highest-risk changes).

The mapping is **graduated by risk**: lower tiers enforce fewer gates and
lighter process, higher tiers layer on maintainability, then security, then
dedicated high-risk gates. Concretely:

* ``LEVEL_1_INTERNAL`` — the four always-on automated gates only; no human
  approval, so these PRs are auto-merge eligible once green.
* ``LEVEL_2_BACKEND_OR_API`` — adds ``maintainability_acceptable``; human
  approval required (backend/API changes touch other systems).
* ``LEVEL_3_USER_FACING`` — adds ``security_acceptable`` and a dedicated
  ``security_review``; human approval required (user-visible surface).
* ``LEVEL_4_HIGH_RISK`` — adds ``migration_reversibility`` on top of LEVEL_3;
  human approval required (auth, payments, schema migrations, etc.).

``ValidationProfile`` subclasses :class:`enum.StrEnum` so JSON serialization
yields the plain string value (see ID-013), matching the
:class:`~sfp_contracts.agents.status.AgentStatus` convention.
"""

from enum import StrEnum
from types import MappingProxyType


class ValidationProfile(StrEnum):
    """The four risk-tiered validation profiles a PR-spec can carry.

    Per ID-067, the Planner assigns exactly one of these to every PR-spec; the
    chosen profile determines which gates the workflow enforces and whether
    human approval is required before merge. When in doubt, choose the higher
    level.
    """

    LEVEL_1_INTERNAL = "LEVEL_1_INTERNAL"
    LEVEL_2_BACKEND_OR_API = "LEVEL_2_BACKEND_OR_API"
    LEVEL_3_USER_FACING = "LEVEL_3_USER_FACING"
    LEVEL_4_HIGH_RISK = "LEVEL_4_HIGH_RISK"


#: The four always-on automated gates enforced at every tier (ID-024).
#: Drawn from the holistic six on
#: :class:`~sfp_contracts.agents.reviewer.QualityGates`.
_BASE_GATES: tuple[str, ...] = (
    "blueprint_compliance",
    "acceptance_criteria_satisfied",
    "test_plan_satisfied",
    "no_unrelated_changes",
)

#: Profile -> the ordered list of gate names the workflow enforces (ID-024 /
#: ID-067). A read-only :class:`~types.MappingProxyType` over tuples, so the
#: mapping is immutable data that can only be changed by editing this module.
GATE_MAPPING: MappingProxyType[ValidationProfile, tuple[str, ...]] = MappingProxyType(
    {
        ValidationProfile.LEVEL_1_INTERNAL: _BASE_GATES,
        ValidationProfile.LEVEL_2_BACKEND_OR_API: _BASE_GATES + ("maintainability_acceptable",),
        ValidationProfile.LEVEL_3_USER_FACING: _BASE_GATES
        + (
            "maintainability_acceptable",
            "security_acceptable",
            "security_review",
        ),
        ValidationProfile.LEVEL_4_HIGH_RISK: _BASE_GATES
        + (
            "maintainability_acceptable",
            "security_acceptable",
            "security_review",
            "migration_reversibility",
        ),
    }
)

#: Profiles that require a human approval before merge (ID-024). LEVEL_1 is
#: intentionally absent — internal PRs are auto-merge eligible once green.
#: This frozenset is the source of truth; :func:`requires_human_approval`
#: delegates to it.
REQUIRES_HUMAN_APPROVAL: frozenset[ValidationProfile] = frozenset(
    {
        ValidationProfile.LEVEL_2_BACKEND_OR_API,
        ValidationProfile.LEVEL_3_USER_FACING,
        ValidationProfile.LEVEL_4_HIGH_RISK,
    }
)


def requires_human_approval(profile: ValidationProfile) -> bool:
    """Return whether ``profile`` requires a human approval before merge.

    Per ID-024, only ``LEVEL_1_INTERNAL`` is auto-merge eligible; every other
    tier requires a human approval. This is a thin, ergonomic accessor over
    :data:`REQUIRES_HUMAN_APPROVAL` (the canonical data).
    """
    return profile in REQUIRES_HUMAN_APPROVAL
