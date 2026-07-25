"""Tests for resolve_context (SFP-66; ID-071).

Covers the acceptance criteria:
- (AC-all-resolved) every required input present -> resolved full, missing empty;
- (AC-one-missing) a single absent input -> its name in missing, the rest in
  resolved, both in declaration order;
- (AC-all-missing) no inputs present -> resolved empty, missing = all names in
  order;
- (AC-ticket_id) ``ticket_id`` is keyword-only (positional raises TypeError) and
  is echoed on the result;
- (AC-determinism) two calls with equal inputs yield equal results;
- (AC-purity) the function mutates neither ``declaration`` nor ``available``;
- (AC-empty) an empty declaration resolves to resolved=[], missing=[].
- Parametrized over DEFAULT_CATALOGUE-derived inputs.
"""

from copy import deepcopy

import pytest
from sfp_contracts.context.bindings import ContextBinding
from sfp_contracts.context.declaration import ContextInput, TicketContextDeclaration
from sfp_contracts.context.types import DEFAULT_CATALOGUE, ContextType, ContextTypeKind
from workspace_worker.workflow.context_resolver import resolve_context

#: The DEFAULT_CATALOGUE entry names, in catalogue order — used to build
#: declarations and available-binding dicts parametrically.
CATALOGUE_NAMES: list[str] = [entry.name for entry in DEFAULT_CATALOGUE.entries]

#: name -> ContextType, for constructing bindings against the catalogue.
_BY_NAME: dict[str, ContextType] = {e.name: e for e in DEFAULT_CATALOGUE.entries}


def make_binding(name: str, source_ticket: str = "SFP-1") -> ContextBinding:
    entry = _BY_NAME[name]
    return ContextBinding(
        name=name,
        context_type=entry,
        value="ref-xyz" if entry.kind is ContextTypeKind.SECRET_REF else "value-xyz",
        source_ticket=source_ticket,
    )


def make_declaration(names: list[str]) -> TicketContextDeclaration:
    return TicketContextDeclaration(
        required_inputs=[ContextInput(name=n, source_ticket="SFP-1") for n in names],
    )


def make_available(names: list[str]) -> dict[str, ContextBinding]:
    return {n: make_binding(n) for n in names}


# --- AC-all-resolved ---


def test_all_inputs_resolved() -> None:
    """Every required input present -> resolved full, missing empty, order kept."""
    names = CATALOGUE_NAMES
    declaration = make_declaration(names)
    available = make_available(names)

    result = resolve_context(declaration, available, ticket_id="SFP-7")

    assert result.ticket_id == "SFP-7"
    assert result.missing == []
    assert [b.name for b in result.resolved] == names


@pytest.mark.parametrize(
    "entry",
    DEFAULT_CATALOGUE.entries,
    ids=[e.name for e in DEFAULT_CATALOGUE.entries],
)
def test_single_input_resolved(entry: ContextType) -> None:
    """A single required input that is present resolves (parametrized over catalogue)."""
    declaration = make_declaration([entry.name])
    available = make_available([entry.name])

    result = resolve_context(declaration, available, ticket_id="SFP-1")

    assert result.resolved[0].name == entry.name
    assert result.missing == []


# --- AC-one-missing ---


def test_one_missing_others_resolved_in_order() -> None:
    """One absent input -> its name in missing, the rest in resolved, in order."""
    names = CATALOGUE_NAMES
    declaration = make_declaration(names)
    # Drop the middle entry from the available bindings.
    present = list(names)
    missing_name = present[len(present) // 2]
    present.remove(missing_name)
    available = make_available(present)

    result = resolve_context(declaration, available, ticket_id="SFP-9")

    assert result.missing == [missing_name]
    assert [b.name for b in result.resolved] == present


# --- AC-all-missing ---


def test_all_missing_resolved_empty_missing_all_in_order() -> None:
    """No inputs present -> resolved empty, missing = all names in order."""
    names = CATALOGUE_NAMES
    declaration = make_declaration(names)

    result = resolve_context(declaration, available={}, ticket_id="SFP-2")

    assert result.resolved == []
    assert result.missing == names


# --- AC-ticket_id ---


def test_ticket_id_keyword_only_and_echoed() -> None:
    """ticket_id is accepted as a keyword arg and echoed on the result."""
    declaration = make_declaration([])
    result = resolve_context(declaration, {}, ticket_id="SFP-66")
    assert result.ticket_id == "SFP-66"


def test_ticket_id_positional_raises_type_error() -> None:
    """Passing ticket_id positionally raises TypeError (it is keyword-only)."""
    declaration = make_declaration([])
    with pytest.raises(TypeError):
        resolve_context(declaration, {}, "SFP-66")  # type: ignore[misc]


# --- AC-determinism ---


@pytest.mark.parametrize(
    "entry",
    DEFAULT_CATALOGUE.entries,
    ids=[e.name for e in DEFAULT_CATALOGUE.entries],
)
def test_resolve_is_deterministic(entry: ContextType) -> None:
    """Two calls with equal inputs yield equal results."""
    declaration = make_declaration([entry.name])
    available = make_available([entry.name])

    a = resolve_context(declaration, available, ticket_id="SFP-1")
    b = resolve_context(declaration, available, ticket_id="SFP-1")

    assert a == b


# --- AC-purity ---


def test_purity_does_not_mutate_inputs() -> None:
    """resolve_context mutates neither declaration nor available."""
    names = CATALOGUE_NAMES
    declaration = make_declaration(names)
    available = make_available(names)
    declaration_before = deepcopy(declaration)
    available_before = deepcopy(available)

    resolve_context(declaration, available, ticket_id="SFP-3")

    assert declaration == declaration_before
    assert declaration.required_inputs == declaration_before.required_inputs
    assert available == available_before
    assert set(available) == set(available_before)


def test_purity_available_not_mutated_on_missing() -> None:
    """available is unchanged even when some inputs are missing."""
    names = CATALOGUE_NAMES
    declaration = make_declaration(names)
    available = make_available(names[:1])  # only the first present
    available_before = deepcopy(available)

    resolve_context(declaration, available, ticket_id="SFP-4")

    assert available == available_before


# --- AC-empty ---


def test_empty_declaration_resolves_clean() -> None:
    """An empty declaration -> resolved=[], missing=[]."""
    declaration = TicketContextDeclaration()
    result = resolve_context(declaration, available={}, ticket_id="SFP-5")

    assert result.resolved == []
    assert result.missing == []
    assert result.ticket_id == "SFP-5"


def test_empty_declaration_ignores_available() -> None:
    """An empty declaration resolves empty even when bindings are available."""
    declaration = TicketContextDeclaration()
    available = make_available(CATALOGUE_NAMES)

    result = resolve_context(declaration, available=available, ticket_id="SFP-6")

    assert result.resolved == []
    assert result.missing == []
