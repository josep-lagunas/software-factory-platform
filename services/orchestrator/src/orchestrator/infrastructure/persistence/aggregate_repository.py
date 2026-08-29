"""SQLAlchemy adapter for the domain :class:`AggregateRepository` Protocol.

Grounded in:
- MAS §9.5 — the domain defines the repository Protocol; infrastructure
  supplies the adapter (the SFP-137 DecisionSink seam pattern applied to
  aggregate persistence).
- ID-058 — per-service ORM classes live under ``infrastructure/persistence/``.
  This adapter owns exactly one concern: translating the domain repository
  contract onto a SQLAlchemy session scope.
- Optimistic consistency — the ``expected_version`` comparison happens here,
  inside one unit of work. A mismatch raises the domain's
  :class:`~orchestrator.domain.aggregate_manager.StaleAggregateError` and the
  transaction is rolled back — nothing is persisted, the error never swallowed.

Adapter shape — deliberately thin (adapt, not redesign):

``base.py`` declares only the declarative ``Base``; it exposes no session
factory or unit-of-work helper (the PRSpec anticipates exactly this and
directs the Coder to wrap the session scope minimally). The adapter therefore
takes a **callable producing an open session** (``session_factory``) and runs
each repository operation inside that one session's transaction. A
``sessionmaker(bind=engine)`` satisfies the shape directly, and tests supply a
``contextmanager`` factory over an in-memory SQLite engine — deterministic, no
network, no wall clock. The adapter never creates a global engine and owns no
process-level resources.

Guard-table storage: the domain ``Aggregate.version`` maps to a dedicated
``(aggregate_type, aggregate_id) -> (payload, version)`` table
(:class:`AggregateVersionRow`). One row per managed aggregate is all the
optimistic guard needs; concrete aggregates (SFP-148+) may persist richer
state in their own tables while the version guard stays central and
auditable. Alembic wiring for this table is deliberately **not** in scope —
the PRSpec excludes migrations — so it registers on its own metadata (not the
service ``Base``) until a migrations ticket adopts it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager

import sqlalchemy as sa
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from orchestrator.domain.aggregate_manager import (
    Aggregate,
    AggregateRepository,
    StaleAggregateError,
)

__all__ = [
    "AggregateVersionRow",
    "SessionFactory",
    "SqlAlchemyAggregateRepository",
]

#: One unit of work: a callable yielding an open :class:`~sqlalchemy.orm.Session`
#: that commits on success and rolls back on error. ``sessionmaker(...)`` and
#: the :func:`session_scope` helper below both satisfy this shape.
SessionFactory = Callable[[], AbstractContextManager[Session]]


@contextmanager
def session_scope(session_factory: Callable[[], Session]) -> Iterator[Session]:
    """Provide a transactional session scope around a bare session factory.

    Commit on clean exit, roll back on any exception, always close. This is
    the minimal wrapper the adapter offers so callers with only an engine can
    build a :data:`SessionFactory` without writing their own unit-of-work.
    """

    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


class _AggregateVersionBase(DeclarativeBase):
    """Private metadata for the guard table (kept off the service ``Base``)."""


class AggregateVersionRow(_AggregateVersionBase):
    """Optimistic-version guard state for one managed aggregate.

    Machinery state, not a business table: one row per managed
    ``(aggregate_type, aggregate_id)`` holding the serialised payload and the
    version the optimistic guard compares against.
    """

    __tablename__ = "aggregate_versions"

    row_id: Mapped[int] = mapped_column(
        sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
        primary_key=True,
        autoincrement=True,
        comment="Surrogate key for the guard row.",
    )
    aggregate_type: Mapped[str] = mapped_column(
        sa.Text(),
        nullable=False,
        index=True,
        comment="Fully-qualified class name of the stored aggregate.",
    )
    aggregate_id: Mapped[str] = mapped_column(
        sa.Text(),
        nullable=False,
        index=True,
        comment="Domain identifier of the stored aggregate (MAS §6.6).",
    )
    payload_json: Mapped[dict[str, object]] = mapped_column(
        sa.JSON(),
        nullable=False,
        comment="Serialised aggregate payload (pydantic ``model_dump``).",
    )
    version: Mapped[int] = mapped_column(
        sa.Integer(),
        nullable=False,
        default=0,
        comment="Optimistic-concurrency version; bumped on every accepted save.",
    )
    created_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
        comment="Row creation timestamp (DB-side; never read by the domain).",
    )
    updated_at: Mapped[sa.DateTime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
        comment="Row last-update timestamp (DB-side; never read by the domain).",
    )

    __table_args__ = (
        sa.UniqueConstraint(
            "aggregate_type",
            "aggregate_id",
            name="uq_aggregate_versions_type_id",
        ),
        {"comment": "Optimistic-version guard rows for managed aggregates."},
    )

    def __repr__(self) -> str:
        return (
            f"AggregateVersionRow(aggregate_type={self.aggregate_type!r}, "
            f"aggregate_id={self.aggregate_id!r}, version={self.version!r})"
        )


#: The adapter's aggregate parameter is declared inline on the class via PEP 695
#: type parameters (``class SqlAlchemyAggregateRepository[A: Aggregate]``), which
#: keeps the genericity self-contained and produces local error messages.


class SqlAlchemyAggregateRepository[A: Aggregate](
    AggregateRepository[A],
):
    """Adapter: one :class:`AggregateRepository` onto one session scope.

    Every public method opens exactly one session via the injected
    :data:`SessionFactory` and commits once on success. The
    ``expected_version`` comparison happens inside that unit of work, so a
    conflict rolls the whole operation back — no partial write survives.
    """

    def __init__(
        self,
        aggregate_type: type[A],
        session_factory: SessionFactory,
    ) -> None:
        self._aggregate_type = aggregate_type
        self._session_factory = session_factory

    @property
    def aggregate_type(self) -> type[A]:
        """The concrete aggregate class this repository stores."""
        return self._aggregate_type

    # --- AggregateRepository -------------------------------------------------

    def load(self, aggregate_id: str) -> A | None:
        """Return the stored aggregate, or ``None`` on a miss.

        The stored payload is revalidated through the bound aggregate class,
        so corrupt data surfaces immediately instead of reaching a rule.
        """
        with self._session() as session:
            row = self._select_row(session, aggregate_id)
            if row is None:
                return None
            return self._aggregate_type.model_validate(row.payload_json)

    def save(self, aggregate: A, *, expected_version: int | None) -> A:
        """Persist ``aggregate`` under the optimistic-version guard.

        Semantics by ``expected_version`` (the single, documented mechanism —
        no hidden dual bookkeeping):

        - ``None`` — the caller observed nothing; this save must **insert**.
          An existing row is a conflict (someone created it first).
        - ``int`` — the stored row must match that version exactly, else
          :class:`StaleAggregateError`.

        On success the stored version advances by one (inserts land at 0) and
        the persisted aggregate — version advanced — is returned. On conflict
        nothing is persisted and the error carries both versions.
        """
        with self._session() as session:
            row = self._select_row(session, aggregate.aggregate_id)
            if row is None:
                if expected_version is not None:
                    raise StaleAggregateError(aggregate.aggregate_id, expected_version, None)
                next_version = 0
                row = AggregateVersionRow(
                    aggregate_type=self._type_name(),
                    aggregate_id=aggregate.aggregate_id,
                    payload_json={},
                    version=next_version,
                )
                session.add(row)
            elif expected_version is None:
                raise StaleAggregateError(aggregate.aggregate_id, None, row.version)
            elif expected_version != row.version:
                raise StaleAggregateError(aggregate.aggregate_id, expected_version, row.version)
            else:
                next_version = row.version + 1

            persisted = aggregate.model_copy(update={"version": next_version})
            row.payload_json = persisted.model_dump(mode="json")
            row.version = next_version
            # The session scope commits exactly once on clean exit; committing
            # here as well would make it twice.
            return persisted

    # --- internals -----------------------------------------------------------

    def _session(self) -> AbstractContextManager[Session]:
        """Open one unit of work via the injected factory."""
        return self._session_factory()

    def _type_name(self) -> str:
        """The fully-qualified class name used as the ``aggregate_type`` key."""
        return f"{self._aggregate_type.__module__}.{self._aggregate_type.__qualname__}"

    def _select_row(self, session: Session, aggregate_id: str) -> AggregateVersionRow | None:
        """Fetch this type's guard row for ``aggregate_id``, if one exists."""
        stmt = sa.select(AggregateVersionRow).where(
            AggregateVersionRow.aggregate_type == self._type_name(),
            AggregateVersionRow.aggregate_id == aggregate_id,
        )
        return session.scalars(stmt).first()
