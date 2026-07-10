"""Tests for the ``UserInteraction`` persistence model (MAS §9.4, ID-058).

Validates the declarative contract: table/schema placement, column set and
order, per-column nullability and types, declarative defaults, primary key,
construction of fully-populated and minimally-populated instances, and
``__repr__``. Assertions are made on ORM metadata and constructed instances
(no live DB round-trip is required to verify this declarative surface).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from communication.infrastructure.persistence import Base, UserInteraction


def test_user_interactions_is_the_only_table_on_the_service_base() -> None:
    """Per-service Base registers Communication tables only (ID-058)."""
    assert set(Base.metadata.tables) == {"business.user_interactions"}


def test_table_and_schema_match_spec() -> None:
    table = UserInteraction.__table__
    assert table.name == "user_interactions"
    assert table.schema == "business"


def test_primary_key_is_interaction_id() -> None:
    """ID-058: the identifier column ``<entity>_id`` is the primary key."""
    table = UserInteraction.__table__
    assert list(table.primary_key.columns) == [table.c.interaction_id]


def test_column_set_and_order_match_model() -> None:
    expected = [
        "interaction_id",
        "user_id",
        "origin",
        "type",
        "response_required",
        "channel",
        "provider_reference",
        "question",
        "summary",
        "last_message_emissor",
        "last_message_timestamp",
        "created_at",
        "expires_at",
        "completed_at",
        "previous_interaction_id",
    ]
    assert [c.name for c in UserInteraction.__table__.columns] == expected


def test_nullable_contract() -> None:
    """Only the three optional fields are nullable; the rest are NOT NULL."""
    optional = {"user_id", "completed_at", "previous_interaction_id"}
    for col in UserInteraction.__table__.columns:
        if col.name in optional:
            assert col.nullable is True, f"{col.name} should be nullable"
        else:
            assert col.nullable is False, f"{col.name} should be NOT NULL"


def test_column_types_match_model() -> None:
    c = UserInteraction.__table__.c
    assert isinstance(c.interaction_id.type, sa.Uuid)
    assert isinstance(c.user_id.type, sa.Uuid)
    assert isinstance(c.origin.type, sa.String)
    assert c.origin.type.length == 20
    assert isinstance(c.type.type, sa.String)
    assert c.type.type.length == 50
    assert isinstance(c.response_required.type, sa.Boolean)
    assert isinstance(c.channel.type, sa.String)
    assert c.channel.type.length == 50
    assert isinstance(c.provider_reference.type, sa.String)
    assert c.provider_reference.type.length == 255
    assert isinstance(c.question.type, sa.Text)
    assert isinstance(c.summary.type, sa.Text)
    assert isinstance(c.last_message_emissor.type, sa.String)
    assert c.last_message_emissor.type.length == 50
    assert isinstance(c.last_message_timestamp.type, sa.DateTime)
    assert c.last_message_timestamp.type.timezone is True
    assert isinstance(c.created_at.type, sa.DateTime)
    assert c.created_at.type.timezone is True
    assert isinstance(c.expires_at.type, sa.DateTime)
    assert c.expires_at.type.timezone is True
    assert isinstance(c.completed_at.type, sa.DateTime)
    assert isinstance(c.previous_interaction_id.type, sa.Uuid)


def test_interaction_id_default_is_uuid4_factory() -> None:
    col = UserInteraction.__table__.c.interaction_id
    assert col.default is not None
    # SA registers the Python-side default as a context-accepting callable
    # (``default=uuid.uuid4`` on the model); evaluating it with a null context
    # the way SA does at insert time must yield a fresh uuid.UUID.
    assert callable(col.default.arg)
    value = col.default.arg(None)
    assert isinstance(value, uuid.UUID)


def test_response_required_default_is_false() -> None:
    col = UserInteraction.__table__.c.response_required
    assert col.default is not None
    assert col.default.arg is False


def test_created_at_has_server_default_now() -> None:
    assert UserInteraction.__table__.c.created_at.server_default is not None


def test_construction_round_trips_all_fields() -> None:
    interaction_id = uuid.uuid4()
    user_id = uuid.uuid4()
    previous_id = uuid.uuid4()
    last_ts = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    expires = datetime(2026, 7, 10, 20, 0, tzinfo=UTC)
    created = datetime(2026, 7, 10, 11, 59, tzinfo=UTC)

    interaction = UserInteraction(
        interaction_id=interaction_id,
        user_id=user_id,
        origin="inbound",
        type="question",
        response_required=True,
        channel="slack",
        provider_reference="C123/T456",
        question="What is SFP?",
        summary="User asked what SFP is.",
        last_message_emissor="user",
        last_message_timestamp=last_ts,
        created_at=created,
        expires_at=expires,
        completed_at=None,
        previous_interaction_id=previous_id,
    )

    assert interaction.interaction_id == interaction_id
    assert interaction.user_id == user_id
    assert interaction.origin == "inbound"
    assert interaction.type == "question"
    assert interaction.response_required is True
    assert interaction.channel == "slack"
    assert interaction.provider_reference == "C123/T456"
    assert interaction.question == "What is SFP?"
    assert interaction.summary == "User asked what SFP is."
    assert interaction.last_message_emissor == "user"
    assert interaction.last_message_timestamp == last_ts
    assert interaction.created_at == created
    assert interaction.expires_at == expires
    assert interaction.completed_at is None
    assert interaction.previous_interaction_id == previous_id


def test_construction_with_optional_fields_none() -> None:
    """The three optional fields may legitimately be omitted/None."""
    last_ts = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    interaction = UserInteraction(
        origin="outbound",
        type="notification",
        response_required=False,
        channel="slack",
        provider_reference="C123/T456",
        question="FYI",
        summary="Notified user.",
        last_message_emissor="agent",
        last_message_timestamp=last_ts,
        expires_at=datetime(2026, 7, 10, 20, 0, tzinfo=UTC),
    )

    assert interaction.user_id is None
    assert interaction.completed_at is None
    assert interaction.previous_interaction_id is None
    assert interaction.origin == "outbound"
    assert interaction.response_required is False


def test_repr_is_informative() -> None:
    interaction = UserInteraction(
        interaction_id=uuid.uuid4(),
        origin="inbound",
        type="question",
        response_required=False,
        channel="slack",
        provider_reference="C123/T456",
        question="q",
        summary="s",
        last_message_emissor="user",
        last_message_timestamp=datetime(2026, 7, 10, 12, 0, tzinfo=UTC),
        expires_at=datetime(2026, 7, 10, 20, 0, tzinfo=UTC),
    )

    text = repr(interaction)
    assert text.startswith("UserInteraction(")
    assert "channel='slack'" in text
    assert "provider_reference='C123/T456'" in text
