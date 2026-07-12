"""The :class:`ValidationProfile` enum — risk tiers that select a PR's gates.

Members are the authoritative four-level set fixed by ID-067. The enum lands
ahead of the rest of SFP-24 (gate-mapping data + human-approval rule) because
:class:`~sfp_contracts.agents.planner.PrSpec` needs it as a typed field today;
ID-067 fully determines the member names and meanings, so no design decision is
made here. SFP-24 extends this module with the profile -> gates mapping.

``ValidationProfile`` subclasses :class:`enum.StrEnum` so JSON serialization
yields the plain string value (see ID-013), matching the
:class:`~sfp_contracts.agents.status.AgentStatus` convention.
"""

from enum import StrEnum


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
