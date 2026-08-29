"""Generic aggregate mutation machinery — transaction-boundary rules (MAS §9.5).

Grounded in:
- MAS §9.5 — every change to an aggregate flows through a *rule* executed
  inside a transaction boundary: the aggregate is loaded, the rule is applied
  to it, and only the rule's result is saved. A rule that raises aborts the
  mutation with **no** write (no partial state ever lands).
- MAS §6.x / ID-058 — storage is an implementation detail of infrastructure;
  the domain defines a :class:`AggregateRepository` *Protocol* and consumes it
  structurally. This module therefore imports **no** persistence library
  (pydantic-only domain discipline; verified by an import-surface test).
- SFP-137 pattern (DecisionSink seam) — protocol lives in the domain, the
  concrete adapter lives in ``infrastructure/persistence/``.
- Optimistic consistency — every save carries the version the caller *saw*
  (``expected_version``). A mismatch means another writer got there first;
  :class:`StaleAggregateError` is raised and **never swallowed**, and nothing
  is persisted.

Shape:

- :class:`Aggregate` — a minimal structural base: an identifier plus a
  monotonically increasing integer ``version``. Concrete aggregates (e.g. the
  Ticket aggregate, SFP-148) subclass it; the manager is generic over them via
  the :data:`~orchestrator.domain.aggregate_manager.AggregateT` TypeVar.
- :class:`AggregateRepository` — the storage seam. ``expected_version`` is an
  explicit parameter on :meth:`~AggregateRepository.save` (not a hidden field
  comparison), because the caller's *view* of the version is exactly what
  optimistic locking must check. Implementations compare it against the stored
  version and raise :class:`StaleAggregateError` on conflict.
- :class:`AggregateManager` — load / save / mutate. ``mutate`` is the
  transaction boundary: ``load`` → ``rule(loaded_or_None)`` → ``save(result)``
  where the expected version is taken from the state that was loaded
  (``None`` on a load miss ⇒ the very first write, upsert semantics).

Determinism (AP-011): the manager introduces no clock, no randomness and no
I/O of its own — all effects flow through the injected repository, so tests
run against a spy/in-memory repository deterministically.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Generic, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

#: The manager's aggregate parameter. Bounding the TypeVar to
#: :class:`Aggregate` gives ``mutate``'s rule a precise
#: ``Callable[[AggregateT | None], AggregateT]`` shape under mypy strict while
#: keeping the manager generic over concrete aggregate classes.
AggregateT = TypeVar("AggregateT", bound="Aggregate")

#: Sentinel meaning "no version has been observed yet". Distinct from ``None``
#: so an optional-vs-sentinel confusion can never silently pass a stale check.
FIRST_WRITE: int = 0


class StaleAggregateError(Exception):
    """An aggregate changed between load and save (optimistic-lock conflict).

    Raised by the repository (and propagated by the manager, never swallowed)
    when the ``expected_version`` a caller carried no longer matches the stored
    version. Carries both versions plus the aggregate id for observability.

    Nothing is persisted when this is raised — the caller must re-load,
    re-apply its rule against fresh state, and retry.
    """

    def __init__(
        self,
        aggregate_id: str,
        expected_version: int | None,
        actual_version: int | None,
    ) -> None:
        self.aggregate_id = aggregate_id
        self.expected_version = expected_version
        self.actual_version = actual_version
        super().__init__(
            f"Stale aggregate {aggregate_id!r}: expected version "
            f"{expected_version!r}, but the stored version is {actual_version!r}"
        )


class Aggregate(BaseModel):
    """Minimal structural base for aggregates managed by
    :class:`AggregateManager`.

    Two fields only — the identity (``aggregate_id``, MAS §6.6 identifiers are
    plain strings at the domain level) and the optimistic-concurrency
    ``version``, bumped by the *rule* (or a domain helper) when it produces a
    new state. The manager never bumps the version itself: the rule owns the
    aggregate's content, the manager owns the transaction boundary.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    aggregate_id: str
    version: int = Field(default=FIRST_WRITE, ge=0)


@runtime_checkable
class AggregateRepository(Protocol[AggregateT]):
    """Storage seam for one aggregate type (MAS §9.5, SFP-137 pattern).

    The domain depends on this Protocol; infrastructure supplies a concrete
    adapter (``SqlAlchemyAggregateRepository``). ``expected_version`` is an
    explicit save parameter — the single, documented version-check mechanism
    (no hidden dual bookkeeping):

    - ``None`` — insert semantics: the save expects *no* stored row yet (the
      caller loaded nothing). If a row exists, that is a conflict.
    - ``int`` — update semantics: the save must match the stored version
      exactly, otherwise :class:`StaleAggregateError`.
    """

    def load(self, aggregate_id: str) -> AggregateT | None:
        """Return the stored aggregate, or ``None`` when nothing is stored.

        A miss is a first-class outcome (upsert semantics): the manager passes
        ``None`` to the mutation rule so a rule can create the aggregate.
        """
        ...  # pragma: no cover

    def save(self, aggregate: AggregateT, *, expected_version: int | None) -> AggregateT:
        """Persist ``aggregate`` if the stored version matches ``expected_version``.

        Returns the persisted aggregate (allowing adapters to normalise
        server-side values). Raises :class:`StaleAggregateError` on mismatch —
        and persists nothing when it does.
        """
        ...  # pragma: no cover


class AggregateManager(Generic[AggregateT]):  # noqa: UP046 – explicit Generic is intentional
    """Applies rules to aggregates inside a transaction boundary (MAS §9.5).

    One instance is bound to one repository (and, implicitly, one aggregate
    type via the TypeVar). The manager enforces exactly one discipline:

    **every change flows through a rule and only the rule's result is saved.**
    Callers never hand the manager a pre-mutated aggregate and never observe a
    partially applied rule — a rule that raises aborts with zero save calls.
    """

    def __init__(self, repository: AggregateRepository[AggregateT]) -> None:
        self._repository = repository

    @property
    def repository(self) -> AggregateRepository[AggregateT]:
        """The injected storage seam (exposed for composition, not mutation)."""
        return self._repository

    def load(self, aggregate_id: str) -> AggregateT | None:
        """Load one aggregate; ``None`` when nothing is stored under the id."""
        return self._repository.load(aggregate_id)

    def save(
        self,
        aggregate: AggregateT,
        *,
        expected_version: int | None,
    ) -> AggregateT:
        """Persist ``aggregate`` under optimistic-version guard.

        ``expected_version`` is the version the caller *observed* (from a
        preceding :meth:`load`, or ``None`` for a first write). A conflict is
        surfaced as :class:`StaleAggregateError` and nothing is persisted.
        """
        return self._repository.save(aggregate, expected_version=expected_version)

    def mutate(
        self,
        aggregate_id: str,
        rule: Callable[[AggregateT | None], AggregateT],
    ) -> AggregateT:
        """Apply ``rule`` to the aggregate ``aggregate_id`` identifies (MAS §9.5).

        The transaction boundary, in order:

        1. **load** the stored aggregate (``None`` on a miss — upsert
           semantics; the rule receives ``None`` and may create).
        2. **apply** the rule to the loaded state. The rule is a pure function
           of the loaded aggregate; it performs no I/O and no persistence.
        3. **save** only the rule's result, with ``expected_version`` taken
           from the state that was loaded (``None`` when nothing was).

        If the rule raises, the exception propagates to the caller and the
        repository's save is **never called** — no partial write can land.
        If the save hits a version conflict, :class:`StaleAggregateError`
        propagates and nothing is persisted.

        Returns:
            The persisted aggregate exactly as the repository saved it.
        """
        loaded = self._repository.load(aggregate_id)
        expected_version = loaded.version if loaded is not None else None
        result = rule(loaded)
        return self._repository.save(result, expected_version=expected_version)
