"""Tests for the generic aggregate-mutation machinery (MAS §9.5, SFP-147).

Covers every acceptance criterion at the domain level, against a spy/in-memory
repository so call counts are asserted *exactly*:

- rule-applies — mutate saves exactly once, with the rule's output;
- rule-raises-no-write — zero saves, exception propagates;
- stale-version — :class:`StaleAggregateError`, nothing saved;
- load-miss — ``load`` returns ``None``; ``mutate`` passes ``None`` to the
  rule (upsert semantics) and saves the returned aggregate;
- import surface — ``orchestrator.domain.aggregate_manager`` references no
  SQLAlchemy/alembic symbol in *code* (AST walk, so docstring prose about
  "SQLAlchemy" can never false-positive).
"""

from __future__ import annotations

import ast
import importlib

import pydantic
import pytest
from orchestrator.domain.aggregate_manager import (
    FIRST_WRITE,
    Aggregate,
    AggregateManager,
    AggregateRepository,
    StaleAggregateError,
)

# --- fixtures ---------------------------------------------------------------


class Widget(Aggregate):
    """A concrete aggregate — one extra field, nothing more."""

    label: str = ""
    counter: int = 0


class SpyRepository:
    """In-memory repository that records every call.

    Deterministic by construction: no clock, no network, no ordering beyond
    the call sequence. ``expected_version`` is enforced exactly as the domain
    Protocol documents, so the manager's semantics are tested against the
    contract rather than against a lenient stub.
    """

    def __init__(self, stored: dict[str, Widget] | None = None) -> None:
        self._stored: dict[str, Widget] = dict(stored or {})
        self.load_calls: list[str] = []
        self.save_calls: list[tuple[Widget, int | None]] = []

    def load(self, aggregate_id: str) -> Widget | None:
        self.load_calls.append(aggregate_id)
        return self._stored.get(aggregate_id)

    def save(self, aggregate: Widget, *, expected_version: int | None) -> Widget:
        self.save_calls.append((aggregate, expected_version))
        existing = self._stored.get(aggregate.aggregate_id)
        if existing is None:
            if expected_version is not None:
                raise StaleAggregateError(aggregate.aggregate_id, expected_version, None)
            persisted = aggregate.model_copy(update={"version": 0})
        elif expected_version is None:
            raise StaleAggregateError(aggregate.aggregate_id, None, existing.version)
        elif expected_version != existing.version:
            raise StaleAggregateError(aggregate.aggregate_id, expected_version, existing.version)
        else:
            persisted = aggregate.model_copy(update={"version": existing.version + 1})
        self._stored[aggregate.aggregate_id] = persisted
        return persisted


class RuleFailed(Exception):
    """Marker the test rules raise — never caught, so it must propagate."""


def bump(widget: Widget | None) -> Widget:
    """A canonical rule: extend the loaded state (or create it on a miss)."""
    if widget is None:
        return Widget(aggregate_id="w1", label="created", counter=1)
    return widget.model_copy(update={"counter": widget.counter + 1, "version": widget.version + 1})


# --- rule-applies: mutate saves exactly the rule's output --------------------


def test_mutate_applies_rule_and_saves_only_the_result() -> None:
    repo = SpyRepository(stored={"w1": Widget(aggregate_id="w1", label="base", version=3)})
    manager: AggregateManager[Widget] = AggregateManager(repo)

    saved = manager.mutate("w1", bump)

    # Exactly one save, carrying exactly the rule's output.
    assert len(repo.save_calls) == 1
    saved_arg, expected_version = repo.save_calls[0]
    assert saved_arg.counter == 1  # rule output, not the loaded state
    assert saved_arg.label == "base"  # loaded state flowed into the rule
    assert expected_version == 3  # taken from the state that was loaded
    # mutate returns the repository's persisted aggregate (the spy's
    # version-bumped copy), not the rule's raw output object.
    assert saved == repo._stored["w1"]
    assert saved.version == 4


def test_mutate_returns_the_repositories_persisted_aggregate() -> None:
    repo = SpyRepository(stored={"w1": Widget(aggregate_id="w1", version=0)})
    manager = AggregateManager(repo)

    saved = manager.mutate("w1", bump)

    # The spy bumps the version on save; the manager hands that back verbatim.
    assert saved.version == 1
    assert repo.load_calls == ["w1"]


def test_mutate_calls_rule_once_with_loaded_state() -> None:
    calls: list[Widget | None] = []

    def observing_rule(widget: Widget | None) -> Widget:
        calls.append(widget)
        return bump(widget)

    repo = SpyRepository(stored={"w1": Widget(aggregate_id="w1", label="seen", version=2)})
    AggregateManager(repo).mutate("w1", observing_rule)

    assert calls == [Widget(aggregate_id="w1", label="seen", version=2)]


# --- rule-raises: zero saves, exception propagates --------------------------


def test_rule_raising_means_zero_saves_and_error_propagates() -> None:
    def failing_rule(widget: Widget | None) -> Widget:
        raise RuleFailed("rule refused")

    repo = SpyRepository(stored={"w1": Widget(aggregate_id="w1", version=5)})
    manager = AggregateManager(repo)

    with pytest.raises(RuleFailed):
        manager.mutate("w1", failing_rule)

    assert repo.load_calls == ["w1"]  # the load happened —
    assert repo.save_calls == []  # — but no write ever did (transaction boundary)


def test_rule_raising_on_load_miss_also_writes_nothing() -> None:
    def failing_rule(widget: Widget | None) -> Widget:
        raise RuleFailed

    repo = SpyRepository()
    with pytest.raises(RuleFailed):
        AggregateManager(repo).mutate("missing", failing_rule)

    assert repo.save_calls == []


def test_any_exception_type_from_the_rule_propagates_untouched() -> None:
    repo = SpyRepository(stored={"w1": Widget(aggregate_id="w1")})

    def refusing_rule(widget: Widget | None) -> Widget:
        raise ValueError("domain refusal")

    with pytest.raises(ValueError, match="domain refusal"):
        AggregateManager(repo).mutate("w1", refusing_rule)

    assert repo.save_calls == []


# --- stale version: StaleAggregateError, nothing persisted -------------------


def test_save_with_stale_expected_version_raises_and_persists_nothing() -> None:
    repo = SpyRepository(stored={"w1": Widget(aggregate_id="w1", version=7)})
    manager = AggregateManager(repo)
    stale_widget = Widget(aggregate_id="w1", version=4)

    with pytest.raises(StaleAggregateError):
        manager.save(stale_widget, expected_version=4)

    assert repo.save_calls == [(stale_widget, 4)]  # the attempt was made —
    assert repo._stored["w1"].version == 7  # — but the stored state is untouched


def test_stale_error_carries_both_versions_for_observability() -> None:
    repo = SpyRepository(stored={"w1": Widget(aggregate_id="w1", version=7)})
    manager = AggregateManager(repo)

    with pytest.raises(StaleAggregateError) as excinfo:
        manager.save(Widget(aggregate_id="w1"), expected_version=2)

    assert excinfo.value.aggregate_id == "w1"
    assert excinfo.value.expected_version == 2
    assert excinfo.value.actual_version == 7


def test_mutate_on_stale_state_surfaces_stale_error() -> None:
    """The rule succeeded but a concurrent writer won — no silent overwrite."""
    repo = SpyRepository(stored={"w1": Widget(aggregate_id="w1", version=7)})

    def saboteur(widget: Widget | None) -> Widget:
        # Simulate a concurrent writer landing after our load.
        repo._stored["w1"] = Widget(aggregate_id="w1", version=8)
        return bump(widget)

    with pytest.raises(StaleAggregateError):
        AggregateManager(repo).mutate("w1", saboteur)

    # Exactly one save attempted (the conflict), and the row kept the winner's state.
    assert len(repo.save_calls) == 1
    assert repo._stored["w1"].version == 8


def test_stale_error_is_never_swallowed_into_a_return_value() -> None:
    repo = SpyRepository(stored={"w1": Widget(aggregate_id="w1", version=1)})
    manager = AggregateManager(repo)

    def winning_rule(widget: Widget | None) -> Widget:
        repo._stored["w1"] = Widget(aggregate_id="w1", version=9)
        return bump(widget)

    with pytest.raises(StaleAggregateError):
        manager.mutate("w1", winning_rule)

    assert repo._stored["w1"].version == 9  # the concurrent winner is intact


# --- load miss: None, and mutate passes None to the rule (upsert) ------------


def test_load_on_missing_id_returns_none() -> None:
    repo = SpyRepository()
    manager = AggregateManager(repo)

    assert manager.load("nope") is None
    assert repo.load_calls == ["nope"]


def test_mutate_on_miss_passes_none_to_rule_and_saves_created_aggregate() -> None:
    seen: list[Widget | None] = []

    def creating_rule(widget: Widget | None) -> Widget:
        seen.append(widget)
        return Widget(aggregate_id="fresh", label="created")

    repo = SpyRepository()
    saved = AggregateManager(repo).mutate("fresh", creating_rule)

    assert seen == [None]  # the rule saw the miss, not a default
    assert len(repo.save_calls) == 1
    saved_arg, expected_version = repo.save_calls[0]
    assert saved_arg.label == "created"
    assert expected_version is None  # nothing was loaded ⇒ first write
    assert saved.version == 0  # first persisted version
    assert saved.label == "created"


def test_mutate_upsert_then_update_round_trip() -> None:
    repo = SpyRepository()
    manager = AggregateManager(repo)

    first = manager.mutate("w1", bump)  # create
    second = manager.mutate("w1", bump)  # update

    assert (first.version, first.counter) == (0, 1)
    assert (second.version, second.counter) == (1, 2)
    assert [expected for _, expected in repo.save_calls] == [None, 0]


# --- save / load surface ----------------------------------------------------


def test_save_first_write_uses_none_expected_version() -> None:
    repo = SpyRepository()
    manager = AggregateManager(repo)

    saved = manager.save(Widget(aggregate_id="new"), expected_version=None)

    assert saved.version == 0
    assert repo.save_calls == [(Widget(aggregate_id="new"), None)]


def test_save_matching_version_advances_it() -> None:
    repo = SpyRepository(stored={"w1": Widget(aggregate_id="w1", version=2)})
    manager = AggregateManager(repo)

    saved = manager.save(Widget(aggregate_id="w1", label="v3"), expected_version=2)

    assert saved.version == 3
    assert repo._stored["w1"].version == 3


def test_save_expected_none_against_existing_row_is_a_conflict() -> None:
    """``None`` means "I saw nothing" — an existing row contradicts that."""
    repo = SpyRepository(stored={"w1": Widget(aggregate_id="w1", version=0)})
    manager = AggregateManager(repo)

    with pytest.raises(StaleAggregateError):
        manager.save(Widget(aggregate_id="w1"), expected_version=None)

    assert repo._stored["w1"].version == 0


def test_load_returns_the_stored_aggregate_verbatim() -> None:
    stored = Widget(aggregate_id="w1", label="kept", counter=9, version=4)
    repo = SpyRepository(stored={"w1": stored})

    assert AggregateManager(repo).load("w1") == stored


def test_repository_property_exposes_the_injected_seam() -> None:
    repo = SpyRepository()
    manager = AggregateManager(repo)

    assert manager.repository is repo


# --- manager is generic over the aggregate type -----------------------------


class Gadget(Aggregate):
    """A second aggregate type — proves the machinery is not Widget-shaped."""

    power: bool = False


def test_manager_is_generic_over_aggregate_types() -> None:
    class GadgetRepo(SpyRepository):
        def load(self, aggregate_id: str) -> Gadget | None:  # type: ignore[override]
            return None

        def save(self, aggregate: Gadget, *, expected_version: int | None) -> Gadget:  # type: ignore[override]
            self.save_calls.append((aggregate, expected_version))  # type: ignore[arg-type]
            return aggregate.model_copy(update={"version": 0})

    repo = GadgetRepo()
    manager: AggregateManager[Gadget] = AggregateManager(repo)  # type: ignore[type-var]

    saved = manager.mutate("g1", lambda g: Gadget(aggregate_id="g1", power=True))

    assert saved.power is True
    assert saved.aggregate_id == "g1"


# --- domain import surface: no persistence libraries in code ----------------

#: Modules the domain aggregate machinery must never reference in code. Prose
#: in docstrings is exempt — the AST walk below sees only executable nodes.
BANNED_IN_DOMAIN_CODE: tuple[str, ...] = (
    "sqlalchemy",
    "alembic",
    "asyncpg",
    "psycopg",
)


def _code_referenced_names(module_name: str) -> set[str]:
    """Identifiers/attributes *code* references — docstrings excluded.

    Mirrors the SFP-143 policy-test helper: a raw substring scan would
    false-positive on this module's own docstring prose ("SQLAlchemy adapter"),
    while the AST cannot see docstrings or comments at all.
    """
    module = importlib.import_module(module_name)
    assert module.__file__ is not None, f"{module_name} has no source file"
    with open(module.__file__, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())

    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    names |= {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {alias.asname or alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            names |= {alias.asname or alias.name for alias in node.names}
    return names


def test_aggregate_manager_module_never_imports_sqlalchemy() -> None:
    referenced = _code_referenced_names("orchestrator.domain.aggregate_manager")
    for banned in BANNED_IN_DOMAIN_CODE:
        assert banned not in referenced, (
            f"orchestrator.domain.aggregate_manager must not reference {banned} in code"
        )


def test_domain_import_chain_introduces_no_persistence_modules() -> None:
    """Importing the domain module must not pull in sqlalchemy/alembic.

    Re-importing in a clean subprocess is the honest check: it observes the
    module's own import chain, unaffected by whatever other tests loaded into
    this process. Deterministic (no network, no clock).
    """
    import subprocess
    import sys

    probe = (
        "import sys, importlib;\n"
        "importlib.import_module('orchestrator.domain.aggregate_manager');\n"
        "leaked = sorted(m for m in sys.modules if m.split('.')[0] in "
        "{'sqlalchemy', 'alembic', 'asyncpg', 'psycopg'});\n"
        "print(','.join(leaked))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=True,
    )
    assert completed.stdout.strip() == "", (
        f"domain import chain leaked persistence modules: {completed.stdout.strip()}"
    )


# --- Aggregate base model ---------------------------------------------------


def test_aggregate_base_defaults_and_freeze() -> None:
    widget = Widget(aggregate_id="w1")

    assert widget.version == FIRST_WRITE
    with pytest.raises(pydantic.ValidationError):
        widget.version = 5  # type: ignore[misc]


def test_aggregate_rejects_unknown_fields() -> None:
    with pytest.raises(pydantic.ValidationError):
        Widget(aggregate_id="w1", surprise=1)  # type: ignore[call-arg]


def test_aggregate_rejects_negative_version() -> None:
    with pytest.raises(pydantic.ValidationError):
        Widget(aggregate_id="w1", version=-1)


def test_stale_error_message_names_the_aggregate_and_versions() -> None:
    error = StaleAggregateError("w1", 2, 7)

    message = str(error)

    assert "w1" in message
    assert "2" in message
    assert "7" in message


def test_runtime_checkable_protocol_is_satisfied_by_spy() -> None:
    assert isinstance(SpyRepository(), AggregateRepository)
