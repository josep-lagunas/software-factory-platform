"""Integration tests: ``SqlAlchemyAggregateRepository`` on a real session scope.

Exercises the adapter against an actual SQLAlchemy session — in-memory SQLite
so the suite stays deterministic (no network, no wall clock, no external DB).
``create_all`` stands in for Alembic because migrations are explicitly out of
scope for SFP-147; the guard table registers on its own metadata, so creating
it here cannot leak into the service ``Base.metadata``/autogenerate surface.

Covers the adapter's half of the acceptance criteria: the version guard
(insert-vs-update semantics, stale conflicts, rollback on conflict), round-trip
fidelity through the bound aggregate class, aggregate-type isolation, and the
:func:`session_scope` unit-of-work wrapper.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pytest
import sqlalchemy as sa
from orchestrator.domain.aggregate_manager import (
    Aggregate,
    AggregateManager,
    StaleAggregateError,
)
from orchestrator.infrastructure.persistence.aggregate_repository import (
    AggregateVersionRow,
    SessionFactory,
    SqlAlchemyAggregateRepository,
    session_scope,
)
from sqlalchemy.orm import Session, sessionmaker


class Widget(Aggregate):
    """A concrete aggregate — one extra field, nothing more."""

    label: str = ""
    counter: int = 0


class Gizmo(Aggregate):
    """A second aggregate type — proves the guard rows are per-type."""

    note: str = ""


@pytest.fixture
def session_factory() -> Iterator[SessionFactory]:
    """A fresh shared in-memory database per test, guard table created.

    A single underlying connection is kept open for the test's duration:
    sqlite's ``:memory:`` database lives and dies with its connection, so
    without this each new session would see an empty store and nothing could
    ever be observed to persist. Still fully deterministic — no network, no
    clock, no cross-test state.
    """
    engine = sa.create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=sa.pool.StaticPool,
    )
    AggregateVersionRow.metadata.create_all(engine)
    connection = engine.connect()
    maker = sessionmaker(bind=connection, expire_on_commit=False)

    @contextmanager
    def factory() -> Iterator[Session]:
        with session_scope(maker) as session:
            yield session

    yield factory

    connection.close()
    engine.dispose()


def make_repo(
    session_factory: SessionFactory,
    aggregate_type: type[Aggregate] = Widget,
) -> SqlAlchemyAggregateRepository[Aggregate]:
    return SqlAlchemyAggregateRepository(aggregate_type, session_factory)


# --- load -------------------------------------------------------------------


def test_load_on_empty_store_returns_none(session_factory: SessionFactory) -> None:
    assert make_repo(session_factory).load("w1") is None


def test_load_round_trips_a_saved_aggregate(session_factory: SessionFactory) -> None:
    repo = make_repo(session_factory)
    saved = repo.save(Widget(aggregate_id="w1", label="first", counter=3), expected_version=None)

    loaded = repo.load("w1")

    assert loaded == saved
    assert loaded is not None
    assert (loaded.label, loaded.counter, loaded.version) == ("first", 3, 0)


# --- save: first write ------------------------------------------------------


def test_first_save_inserts_at_version_zero(session_factory: SessionFactory) -> None:
    saved = make_repo(session_factory).save(Widget(aggregate_id="w1"), expected_version=None)

    assert saved.version == 0


def test_first_save_persists_a_guard_row(session_factory: SessionFactory) -> None:
    repo = make_repo(session_factory)
    repo.save(Widget(aggregate_id="w1", label="x"), expected_version=None)

    with session_factory() as session:
        rows = session.scalars(sa.select(AggregateVersionRow)).all()

    assert len(rows) == 1
    assert rows[0].aggregate_id == "w1"
    assert rows[0].version == 0
    assert rows[0].payload_json["label"] == "x"


def test_save_expected_int_when_row_missing_is_stale(session_factory: SessionFactory) -> None:
    """The caller saw a version, but nothing is stored — that is a conflict."""
    repo = make_repo(session_factory)

    with pytest.raises(StaleAggregateError) as excinfo:
        repo.save(Widget(aggregate_id="ghost"), expected_version=3)

    assert excinfo.value.expected_version == 3
    assert excinfo.value.actual_version is None


# --- save: update + version guard -------------------------------------------


def test_save_with_matching_version_advances_and_persists(session_factory: SessionFactory) -> None:
    repo = make_repo(session_factory)
    repo.save(Widget(aggregate_id="w1", label="v0"), expected_version=None)

    updated = repo.save(Widget(aggregate_id="w1", label="v1"), expected_version=0)

    assert updated.version == 1
    loaded = repo.load("w1")
    assert loaded is not None
    assert loaded.label == "v1"


def test_save_with_stale_version_raises_and_persists_nothing(
    session_factory: SessionFactory,
) -> None:
    repo = make_repo(session_factory)
    repo.save(Widget(aggregate_id="w1", label="v0"), expected_version=None)
    repo.save(Widget(aggregate_id="w1", label="v1"), expected_version=0)  # row now at 1

    with pytest.raises(StaleAggregateError) as excinfo:
        repo.save(Widget(aggregate_id="w1", label="stale"), expected_version=0)

    assert excinfo.value.expected_version == 0
    assert excinfo.value.actual_version == 1
    # Nothing from the rejected save landed.
    loaded = repo.load("w1")
    assert loaded is not None
    assert loaded.label == "v1"
    assert loaded.version == 1


def test_save_expected_none_against_existing_row_is_stale(session_factory: SessionFactory) -> None:
    """``None`` asserts "no row exists" — a present row contradicts it."""
    repo = make_repo(session_factory)
    repo.save(Widget(aggregate_id="w1"), expected_version=None)

    with pytest.raises(StaleAggregateError) as excinfo:
        repo.save(Widget(aggregate_id="w1"), expected_version=None)

    assert excinfo.value.actual_version == 0


def test_stale_save_leaves_exactly_one_guard_row(session_factory: SessionFactory) -> None:
    repo = make_repo(session_factory)
    repo.save(Widget(aggregate_id="w1"), expected_version=None)

    with pytest.raises(StaleAggregateError):
        repo.save(Widget(aggregate_id="w1", counter=9), expected_version=42)

    with session_factory() as session:
        assert len(session.scalars(sa.select(AggregateVersionRow)).all()) == 1


# --- aggregate-type isolation -----------------------------------------------


def test_rows_of_different_aggregate_types_are_isolated(session_factory: SessionFactory) -> None:
    widget_repo = make_repo(session_factory, Widget)
    gizmo_repo = make_repo(session_factory, Gizmo)

    widget_repo.save(Widget(aggregate_id="shared"), expected_version=None)
    gizmo_repo.save(Gizmo(aggregate_id="shared", note="mine"), expected_version=None)

    # Same id, two types — two independent guard rows, no cross-talk.
    widget = widget_repo.load("shared")
    gizmo = gizmo_repo.load("shared")
    assert widget is not None and gizmo is not None
    assert isinstance(widget, Widget)
    assert isinstance(gizmo, Gizmo)
    assert gizmo.note == "mine"


def test_repository_exposes_its_bound_aggregate_type(session_factory: SessionFactory) -> None:
    repo = make_repo(session_factory, Gizmo)

    assert repo.aggregate_type is Gizmo


def test_guard_row_repr_is_informative(session_factory: SessionFactory) -> None:
    repo = make_repo(session_factory)
    repo.save(Widget(aggregate_id="w1", label="x"), expected_version=None)

    with session_factory() as session:
        row = session.scalars(sa.select(AggregateVersionRow)).one()

    text = repr(row)

    assert text.startswith("AggregateVersionRow(")
    assert "aggregate_id='w1'" in text
    assert "version=0" in text


# --- the manager on top of the real adapter (end-to-end) --------------------


def test_manager_mutate_through_the_real_adapter(session_factory: SessionFactory) -> None:
    manager = AggregateManager(make_repo(session_factory))

    first = manager.mutate("w1", lambda w: Widget(aggregate_id="w1", label="created", counter=1))
    second = manager.mutate(
        "w1", lambda w: (w or Widget(aggregate_id="w1")).model_copy(update={"counter": 5})
    )

    assert (first.version, first.counter) == (0, 1)
    assert (second.version, second.counter) == (1, 5)


def test_manager_rule_failure_through_real_adapter_writes_nothing(
    session_factory: SessionFactory,
) -> None:
    manager = AggregateManager(make_repo(session_factory))

    def broken(widget: Widget | None) -> Widget:
        raise RuntimeError("rule refused")

    with pytest.raises(RuntimeError, match="rule refused"):
        manager.mutate("w1", broken)

    assert manager.load("w1") is None
    with session_factory() as session:
        assert session.scalars(sa.select(AggregateVersionRow)).all() == []


def test_manager_stale_conflict_through_real_adapter(session_factory: SessionFactory) -> None:
    repo = make_repo(session_factory)
    manager = AggregateManager(repo)
    manager.save(Widget(aggregate_id="w1", label="live"), expected_version=None)

    # A second manager with the same view as version 0 — the row is now at 0,
    # so this save matches. Move the row on first to force the conflict.
    manager.save(Widget(aggregate_id="w1", label="live-2"), expected_version=0)

    with pytest.raises(StaleAggregateError):
        manager.save(Widget(aggregate_id="w1", label="too-late"), expected_version=0)

    loaded = manager.load("w1")
    assert loaded is not None
    assert loaded.label == "live-2"


# --- session_scope ----------------------------------------------------------


def _type_key(aggregate_type: type[Aggregate]) -> str:
    """Mirror the adapter's documented ``aggregate_type`` key for raw-row tests."""
    return f"{aggregate_type.__module__}.{aggregate_type.__qualname__}"


def test_session_scope_commits_on_success(session_factory: SessionFactory) -> None:
    with session_factory() as session:
        session.add(
            AggregateVersionRow(
                aggregate_type=_type_key(Widget),
                aggregate_id="direct",
                payload_json={"aggregate_id": "direct", "label": "direct"},
                version=0,
            )
        )

    loaded = make_repo(session_factory).load("direct")
    assert loaded is not None
    assert loaded.label == "direct"


def test_session_scope_rolls_back_on_error(session_factory: SessionFactory) -> None:
    with pytest.raises(RuntimeError, match="boom"):
        with session_factory() as session:
            session.add(
                AggregateVersionRow(
                    aggregate_type=_type_key(Widget),
                    aggregate_id="doomed",
                    payload_json={"aggregate_id": "doomed", "label": "doomed"},
                    version=0,
                )
            )
            raise RuntimeError("boom")

    assert make_repo(session_factory).load("doomed") is None


def test_session_scope_ends_the_unit_of_work(session_factory: SessionFactory) -> None:
    """After the scope exits, its transaction is closed.

    (SQLAlchemy 2.0 ``Session.close()`` releases the transactional/connection
    state but does not poison the object, and sessions are lazily
    transactional — a transaction begins on first use, not on construction.
    So the honest observables are: a transaction exists once work happens,
    and none remains after the scope exits.)
    """
    seen: list[Session] = []
    with session_factory() as session:
        seen.append(session)
        session.get(AggregateVersionRow, 1)  # begin work ⇒ a transaction exists
        assert session.in_transaction()

    assert seen[0].in_transaction() is False

    # And a subsequent scope gets a fresh, working unit of work.
    with session_factory() as session:
        session.get(AggregateVersionRow, 1)
        assert session.in_transaction()
