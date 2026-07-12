"""Validation contracts: risk-tiered profiles and their gate mappings.

The :class:`ValidationProfile` enum is introduced here as the typed field used by
the planner output (SFP-14), because a PR-spec's ``validation_profile`` must
reference a single canonical enum (ID-067). The full profile -> required-gates
mapping and the human-approval rule are added by SFP-24; only the enum members
themselves are fixed by ID-067 and are therefore safe to land first.
"""
