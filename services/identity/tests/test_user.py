"""Tests for the ``User`` persistence model (MAS §9.3, ID-058).

Validates the declarative contract: table/schema placement, column set and
order, per-column nullability and types, declarative defaults, primary key,
``server_default`` on timestamps, absence of trigger/onupdate mechanism,
construction, and ``__repr__``. Assertions are made on ORM metadata and
constructed instances (no live DB round-trip is required).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from identity.infrastructure.persistence import User


def test_users_table_and_schema_match_spec() -> None:
    table = User.__table__
    assert table.name == "users"
    assert table.schema == "business"


def test_user_id_is_primary_key() -> None:
    """ID-058: the identifier column ``<entity>_id`` is the primary key."""
    table = User.__table__
    assert list(table.primary_key.columns) == [table.c.user_id]


def test_column_set_and_order_match_spec() -> None:
    expected = ["user_id", "created_at", "updated_at"]
    assert [c.name for c in User.__table__.columns] == expected


def test_all_columns_are_not_nullable() -> None:
    for col in User.__table__.columns:
        assert col.nullable is False, f"{col.name} should be NOT NULL"


def test_column_types_match_spec() -> None:
    c = User.__table__.c
    assert isinstance(c.user_id.type, sa.Uuid)
    assert isinstance(c.created_at.type, sa.DateTime)
    assert c.created_at.type.timezone is True
    assert isinstance(c.updated_at.type, sa.DateTime)
    assert c.updated_at.type.timezone is True


def test_user_id_default_is_uuid4_factory() -> None:
    col = User.__table__.c.user_id
    assert col.default is not None
    assert callable(col.default.arg)
    value = col.default.arg(None)
    assert isinstance(value, uuid.UUID)


def test_created_at_has_server_default_now() -> None:
    assert User.__table__.c.created_at.server_default is not None


def test_updated_at_has_server_default_now() -> None:
    assert User.__table__.c.updated_at.server_default is not None


def test_updated_at_has_no_onupdate_and_no_python_default() -> None:
    """SFP-111 mirrors EndpointConfig: server_default only, no trigger/onupdate."""
    col = User.__table__.c.updated_at
    assert col.onupdate is None
    assert col.default is None


def test_construction_round_trips_all_fields() -> None:
    user_id = uuid.uuid4()
    created = datetime(2026, 7, 23, 10, 0, tzinfo=UTC)
    updated = datetime(2026, 7, 23, 10, 30, tzinfo=UTC)

    user = User(user_id=user_id, created_at=created, updated_at=updated)

    assert user.user_id == user_id
    assert user.created_at == created
    assert user.updated_at == updated


def test_repr_is_informative() -> None:
    user = User(user_id=uuid.uuid4())
    text = repr(user)
    assert text.startswith("User(")
    assert "user_id=" in text
