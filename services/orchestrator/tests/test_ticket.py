"""Tests for the ``Ticket`` persistence model + ``WorkflowStatus`` enum.

Validates the declarative contract grounded in MAS §8.4 / §6.5 / §6.6 and
ID-058: the enum enumerates exactly the pinned §8.4 states in order; the table
placement (``business.tickets``), column set and order, per-column nullability
and types, the UUID primary-key default, the identifier-only (no-FK)
``project_id``, the unique+indexed ``external_ref``, ``server_default`` on
timestamps with no ``onupdate``, construction, ``__repr__``, and registration on
the Orchestrator ``Base.metadata``. Assertions are made on ORM metadata and
constructed instances (no live DB round-trip is required).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from orchestrator.infrastructure.persistence import Base, Ticket, WorkflowStatus

# The MAS §8.4 states, in their pinned order.
_EXPECTED_STATES = [
    "READY_FOR_PR_SPECIFICATION",
    "READY_FOR_CODING",
    "CODING_IN_PROGRESS",
    "REVIEW_IN_PROGRESS",
    "WAITING_FOR_USER",
    "READY_FOR_MERGE",
    "MERGING",
    "DEPLOYING",
    "COMPLETED",
    "FAILED",
]


# --- WorkflowStatus enum (MAS §8.4) -----------------------------------------


def test_workflow_status_members_exactly_match_mas_8_4() -> None:
    names = {member.name for member in WorkflowStatus}
    assert names == set(_EXPECTED_STATES)


def test_workflow_status_order_is_pinned() -> None:
    assert [member.name for member in WorkflowStatus] == _EXPECTED_STATES


def test_workflow_status_has_exactly_ten_members() -> None:
    assert len(list(WorkflowStatus)) == 10


def test_workflow_status_is_a_pep_435_enum() -> None:
    import enum

    assert issubclass(WorkflowStatus, enum.Enum)


# --- table + schema placement ----------------------------------------------


def test_tickets_table_and_schema_match_spec() -> None:
    table = Ticket.__table__
    assert table.name == "tickets"
    assert table.schema == "business"


def test_ticket_id_is_primary_key() -> None:
    """ID-058: the identifier column ``<entity>_id`` is the primary key."""
    table = Ticket.__table__
    assert list(table.primary_key.columns) == [table.c.ticket_id]


def test_column_set_and_order_match_spec() -> None:
    expected = [
        "ticket_id",
        "project_id",
        "workflow_status",
        "external_ref",
        "created_at",
        "updated_at",
    ]
    assert [c.name for c in Ticket.__table__.columns] == expected


def test_all_columns_are_not_nullable() -> None:
    for col in Ticket.__table__.columns:
        assert col.nullable is False, f"{col.name} should be NOT NULL"


# --- column types ----------------------------------------------------------


def test_column_types_match_spec() -> None:
    c = Ticket.__table__.c
    assert isinstance(c.ticket_id.type, sa.Uuid)
    assert isinstance(c.project_id.type, sa.Uuid)
    assert isinstance(c.workflow_status.type, sa.Enum)
    assert isinstance(c.external_ref.type, sa.String)
    assert isinstance(c.created_at.type, sa.DateTime)
    assert c.created_at.type.timezone is True
    assert isinstance(c.updated_at.type, sa.DateTime)
    assert c.updated_at.type.timezone is True


def test_workflow_status_enum_stores_mas_8_4_member_names() -> None:
    """The Enum column stores the MAS §8.4 state names and maps the enum."""
    col_type = Ticket.__table__.c.workflow_status.type
    assert isinstance(col_type, sa.Enum)
    assert list(col_type.enums) == _EXPECTED_STATES
    assert col_type.enum_class is WorkflowStatus


# --- identifiers: PK default + no-FK project_id ----------------------------


def test_ticket_id_default_is_uuid4_factory() -> None:
    col = Ticket.__table__.c.ticket_id
    assert col.default is not None
    assert callable(col.default.arg)
    value = col.default.arg(None)
    assert isinstance(value, uuid.UUID)


def test_project_id_has_no_foreign_key() -> None:
    """MAS §6.5: project_id is an identifier reference, NOT a foreign key."""
    col = Ticket.__table__.c.project_id
    assert col.foreign_keys == set()
    # And no table-level FK references project_id either.
    for constraint in Ticket.__table__.constraints:
        assert not any(
            "project_id" in (getattr(fk.column, "name", "") or "") or fk.parent is col
            for fk in getattr(constraint, "elements", []) or []
        ), "project_id must not participate in any foreign-key constraint"


# --- external_ref: non-null, unique, indexed -------------------------------


def test_external_ref_is_not_nullable() -> None:
    assert Ticket.__table__.c.external_ref.nullable is False


def test_external_ref_has_unique_index() -> None:
    """external_ref must be both UNIQUE and INDEXED (single named unique index).

    The Orchestrator Base has no naming_convention, so the index name is
    supplied explicitly: ``ix_tickets_external_ref``.
    """
    indexes = {ix.name: ix for ix in Ticket.__table__.indexes}
    ix = indexes["ix_tickets_external_ref"]
    assert ix.unique is True
    assert [c.name for c in ix.columns] == ["external_ref"]


# --- timestamps: server_default only, no onupdate --------------------------


def test_created_at_has_server_default_now() -> None:
    assert Ticket.__table__.c.created_at.server_default is not None


def test_updated_at_has_server_default_now() -> None:
    assert Ticket.__table__.c.updated_at.server_default is not None


def test_updated_at_has_no_onupdate_and_no_python_default() -> None:
    """ID-058: server_default only, no trigger/onupdate (deferred follow-up)."""
    col = Ticket.__table__.c.updated_at
    assert col.onupdate is None
    assert col.default is None


# --- construction + repr ---------------------------------------------------


def test_construction_round_trips_all_fields() -> None:
    ticket_id = uuid.uuid4()
    project_id = uuid.uuid4()
    created = datetime(2026, 8, 7, 10, 0, tzinfo=UTC)
    updated = datetime(2026, 8, 7, 10, 30, tzinfo=UTC)

    ticket = Ticket(
        ticket_id=ticket_id,
        project_id=project_id,
        workflow_status=WorkflowStatus.READY_FOR_CODING,
        external_ref="SFP-101",
        created_at=created,
        updated_at=updated,
    )

    assert ticket.ticket_id == ticket_id
    assert ticket.project_id == project_id
    assert ticket.workflow_status is WorkflowStatus.READY_FOR_CODING
    assert ticket.external_ref == "SFP-101"
    assert ticket.created_at == created
    assert ticket.updated_at == updated


def test_repr_is_informative() -> None:
    ticket = Ticket(ticket_id=uuid.uuid4(), external_ref="SFP-101")
    text = repr(ticket)
    assert text.startswith("Ticket(")
    assert "ticket_id=" in text
    assert "external_ref=" in text


# --- registration on Orchestrator Base.metadata ----------------------------


def test_ticket_is_registered_on_base_metadata() -> None:
    """Importing Base must register business.tickets on Base.metadata.

    This verifies the registration wiring (persistence/__init__.py imports the
    models package), not just file presence.
    """
    tables = {t.name: t for t in Base.metadata.sorted_tables}
    assert "tickets" in tables
    assert tables["tickets"].schema == "business"
