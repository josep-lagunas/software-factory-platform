"""Tests for the ``UserExternalIdentity`` persistence model (MAS §9.3, ID-058).

Validates the declarative contract: table/schema placement, column set and
order, per-column nullability and types, declarative defaults, primary key,
foreign-key target, unique constraint, ``server_default`` on timestamps,
absence of trigger/onupdate mechanism, construction, and ``__repr__``.
Assertions are made on ORM metadata and constructed instances (no live DB
round-trip is required).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from identity.infrastructure.persistence import UserExternalIdentity


def test_table_and_schema_match_spec() -> None:
    table = UserExternalIdentity.__table__
    assert table.name == "user_external_identities"
    assert table.schema == "business"


def test_external_identity_id_is_primary_key() -> None:
    table = UserExternalIdentity.__table__
    assert list(table.primary_key.columns) == [table.c.external_identity_id]


def test_column_set_and_order_match_spec() -> None:
    expected = [
        "external_identity_id",
        "provider",
        "provider_user_id",
        "user_id",
        "created_at",
        "updated_at",
    ]
    assert [c.name for c in UserExternalIdentity.__table__.columns] == expected


def test_all_columns_are_not_nullable() -> None:
    for col in UserExternalIdentity.__table__.columns:
        assert col.nullable is False, f"{col.name} should be NOT NULL"


def test_column_types_match_spec() -> None:
    c = UserExternalIdentity.__table__.c
    assert isinstance(c.external_identity_id.type, sa.Uuid)
    assert isinstance(c.provider.type, sa.String)
    assert c.provider.type.length is None
    assert isinstance(c.provider_user_id.type, sa.String)
    assert c.provider_user_id.type.length is None
    assert isinstance(c.user_id.type, sa.Uuid)
    assert isinstance(c.created_at.type, sa.DateTime)
    assert c.created_at.type.timezone is True
    assert isinstance(c.updated_at.type, sa.DateTime)
    assert c.updated_at.type.timezone is True


def test_external_identity_id_default_is_uuid4_factory() -> None:
    col = UserExternalIdentity.__table__.c.external_identity_id
    assert col.default is not None
    assert callable(col.default.arg)
    value = col.default.arg(None)
    assert isinstance(value, uuid.UUID)


def test_timestamps_have_server_default() -> None:
    assert UserExternalIdentity.__table__.c.created_at.server_default is not None
    assert UserExternalIdentity.__table__.c.updated_at.server_default is not None


def test_updated_at_has_no_onupdate_and_no_python_default() -> None:
    """SFP-111 mirrors EndpointConfig: server_default only, no trigger/onupdate."""
    col = UserExternalIdentity.__table__.c.updated_at
    assert col.onupdate is None
    assert col.default is None


def test_user_id_foreign_key_targets_business_users() -> None:
    col = UserExternalIdentity.__table__.c.user_id
    fks = list(col.foreign_keys)
    assert len(fks) == 1
    fk = fks[0]
    assert fk.target_fullname == "business.users.user_id"


def test_unique_constraint_on_provider_and_provider_user_id() -> None:
    table = UserExternalIdentity.__table__
    found = None
    for constraint in table.constraints:
        if (
            isinstance(constraint, sa.UniqueConstraint)
            and constraint.name == "uq_provider_provider_user_id"
        ):
            found = constraint
            break
    assert found is not None, "uq_provider_provider_user_id constraint not found"
    assert [c.name for c in found.columns] == ["provider", "provider_user_id"]


def test_construction_round_trips_all_fields() -> None:
    eid_id = uuid.uuid4()
    user_id = uuid.uuid4()
    created = datetime(2026, 7, 23, 10, 0, tzinfo=UTC)
    updated = datetime(2026, 7, 23, 10, 30, tzinfo=UTC)

    ext = UserExternalIdentity(
        external_identity_id=eid_id,
        provider="github",
        provider_user_id="12345",
        user_id=user_id,
        created_at=created,
        updated_at=updated,
    )

    assert ext.external_identity_id == eid_id
    assert ext.provider == "github"
    assert ext.provider_user_id == "12345"
    assert ext.user_id == user_id
    assert ext.created_at == created
    assert ext.updated_at == updated


def test_repr_is_informative() -> None:
    ext = UserExternalIdentity(
        external_identity_id=uuid.uuid4(),
        provider="github",
        provider_user_id="12345",
        user_id=uuid.uuid4(),
    )
    text = repr(ext)
    assert text.startswith("UserExternalIdentity(")
    assert "provider='github'" in text
    assert "provider_user_id='12345'" in text
