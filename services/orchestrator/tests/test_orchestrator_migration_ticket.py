"""Tests for the 0002 ``create business.tickets`` migration (MAS §8.4, ID-058).

Validates the Alembic migration that creates the ``business.tickets`` table for
the ``Ticket`` persistence model: revision identifiers, and upgrade/downgrade
EXECUTED via stubbed ``op`` call recorders (not source-inspected, so the
``op.create_table`` / ``op.create_index`` / ``op.drop_*`` bodies contribute to
coverage), asserting the created table's columns, the native ``workflowstatus``
enum values, the unique named index on ``external_ref``, and the
identifier-only (no-FK) ``project_id``. No live DB is required.
"""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import op as alembic_op

_SERVICE_ROOT = Path(__file__).resolve().parent.parent
_PERSISTENCE = _SERVICE_ROOT / "src" / "orchestrator" / "infrastructure" / "persistence"
_MIGRATION_PATH = _PERSISTENCE / "migrations" / "versions" / "0002_create_ticket.py"

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


def _load_migration_module() -> object:
    spec = importlib.util.spec_from_file_location(
        "orchestrator_migration_0002_create_ticket", _MIGRATION_PATH
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


migration = _load_migration_module()


# --- migration module structure --------------------------------------------


def test_migration_module_imports_successfully() -> None:
    assert hasattr(migration, "upgrade")
    assert hasattr(migration, "downgrade")


def test_revision_identifiers() -> None:
    assert migration.revision == "0002"  # type: ignore[attr-defined]
    assert migration.down_revision == "0001"  # type: ignore[attr-defined]


# --- executed upgrade/downgrade (stubbed op recorder) ----------------------
# Executing upgrade()/downgrade() with stubbed op.* COVERS the op bodies;
# source-inspection alone would leave them unexecuted and dilute coverage.


def _stub_op(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, tuple, dict]]:
    """Record every ``op.<name>`` call as (name, args, kwargs)."""
    calls: list[tuple[str, tuple, dict]] = []

    def _make(name: str) -> object:
        def _fn(*args: object, **kwargs: object) -> None:
            calls.append((name, args, kwargs))

        return _fn

    for op_name in (
        "create_table",
        "create_index",
        "drop_index",
        "drop_table",
    ):
        monkeypatch.setattr(alembic_op, op_name, _make(op_name))
    return calls


def _upgrade_columns(monkeypatch: pytest.MonkeyPatch) -> dict[str, sa.Column]:
    calls = _stub_op(monkeypatch)
    migration.upgrade()  # type: ignore[operator]
    _name, args, _kwargs = [c for c in calls if c[0] == "create_table"][0]
    return {col.name: col for col in args[1:]}


def test_upgrade_creates_business_tickets_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _stub_op(monkeypatch)
    migration.upgrade()  # type: ignore[operator]

    create_table_calls = [c for c in calls if c[0] == "create_table"]
    assert len(create_table_calls) == 1
    _name, args, kwargs = create_table_calls[0]
    assert args[0] == "tickets"
    assert kwargs.get("schema") == "business"


def test_upgrade_table_has_expected_columns_and_types(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    by_name = _upgrade_columns(monkeypatch)

    assert set(by_name) == {
        "ticket_id",
        "project_id",
        "workflow_status",
        "external_ref",
        "created_at",
        "updated_at",
    }

    # ticket_id: UUID primary key.
    assert isinstance(by_name["ticket_id"].type, sa.Uuid)
    assert by_name["ticket_id"].primary_key is True

    # project_id: UUID, not null, NO foreign key (MAS §6.5).
    assert isinstance(by_name["project_id"].type, sa.Uuid)
    assert by_name["project_id"].nullable is False
    assert by_name["project_id"].foreign_keys == set()

    # workflow_status: native enum named workflowstatus with the §8.4 states.
    ws_type = by_name["workflow_status"].type
    assert isinstance(ws_type, sa.Enum)
    assert ws_type.name == "workflowstatus"
    assert list(ws_type.enums) == _EXPECTED_STATES
    assert by_name["workflow_status"].nullable is False

    # external_ref: String, not null.
    assert isinstance(by_name["external_ref"].type, sa.String)
    assert by_name["external_ref"].nullable is False

    # timestamps: DateTime(timezone=True), not null, server_default now.
    for ts in ("created_at", "updated_at"):
        col = by_name[ts]
        assert isinstance(col.type, sa.DateTime)
        assert col.type.timezone is True
        assert col.nullable is False
        assert col.server_default is not None


def test_upgrade_creates_unique_named_index_on_external_ref(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _stub_op(monkeypatch)
    migration.upgrade()  # type: ignore[operator]

    create_index_calls = [c for c in calls if c[0] == "create_index"]
    assert len(create_index_calls) == 1
    _name, args, kwargs = create_index_calls[0]
    assert args[0] == "ix_tickets_external_ref"
    assert args[1] == "tickets"
    assert list(args[2]) == ["external_ref"]
    assert kwargs.get("unique") is True
    assert kwargs.get("schema") == "business"


def test_upgrade_does_not_recreate_business_schema() -> None:
    """The 0001 baseline owns schema creation; 0001 is re-runnable already."""
    source = inspect.getsource(migration.upgrade)  # type: ignore[arg-type]
    assert "CREATE SCHEMA" not in source


def test_downgrade_drops_index_then_table(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_op(monkeypatch)
    migration.downgrade()  # type: ignore[operator]
    # Index must be dropped before the table it belongs to.
    assert [c[0] for c in calls] == ["drop_index", "drop_table"]


def test_downgrade_drops_index_with_business_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _stub_op(monkeypatch)
    migration.downgrade()  # type: ignore[operator]

    drop_index_calls = [c for c in calls if c[0] == "drop_index"]
    assert len(drop_index_calls) == 1
    _name, args, kwargs = drop_index_calls[0]
    assert "ix_tickets_external_ref" in args
    assert kwargs.get("table_name") == "tickets"
    assert kwargs.get("schema") == "business"


def test_downgrade_drops_tickets_table_with_business_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _stub_op(monkeypatch)
    migration.downgrade()  # type: ignore[operator]

    drop_table_calls = [c for c in calls if c[0] == "drop_table"]
    assert len(drop_table_calls) == 1
    _name, args, kwargs = drop_table_calls[0]
    assert args[0] == "tickets"
    assert kwargs.get("schema") == "business"
