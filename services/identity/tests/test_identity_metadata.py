"""Tests for the Identity Service ``Base.metadata`` (ID-058).

Validates that the per-service declarative ``Base`` registers exactly the two
Identity tables and that both live in the ``business`` schema. No live DB is
required — these are pure ORM-metadata assertions.
"""

from __future__ import annotations

from identity.infrastructure.persistence import Base


def test_base_metadata_has_exactly_the_two_identity_tables() -> None:
    """Per-service Base registers Identity tables only (ID-058)."""
    assert set(Base.metadata.tables) == {
        "business.users",
        "business.user_external_identities",
    }


def test_all_identity_tables_use_business_schema() -> None:
    """Every Identity ORM table lives in the ``business`` schema (ID-058)."""
    for table in Base.metadata.tables.values():
        assert table.schema == "business"
