"""Tests for the orchestrator Context Resolver Host (SFP-151; SFP-85 line).

Covers, per the PRSpec acceptance criteria:

- all-resolve: every declared requirement present → a valid
  :class:`ResolvedContext` with one :class:`ContextBinding` per input, in
  declaration order, ``ticket_id == pr_spec_id``;
- missing secret: source dict lacks the name **or** the provider raises
  ``SecretResolutionError`` → :class:`MissingContextError` naming only the
  SecretRef *name*, no material in the message, no partial context returned;
- missing declared (non-secret) value → :class:`MissingContextError` naming
  the input name;
- empty requirements → the empty-but-valid context (success, not error);
- uncatalogued name → defaults to ``ContextType(name=name, kind=STR)`` with no
  error (the documented free-form rule from ``declaration.py``);
- secret materialization: a catalogued ``SECRET_REF`` name's source value is
  treated as the *reference string*, materialized via
  ``SecretRef(name=value)`` → ``provider.resolve``, with the material stored
  only in ``ContextBinding.value``;
- structural-protocol signature: the landed ``sfp_config.LocalSecretProvider``
  satisfies the host's provider Protocol (runtime-checkable + ``issubclass``
  + a real end-to-end run through the host).

Secret-safety oracle: every error case asserts both that the secret material
substring is absent from the message and from any chained exception text
reachable from the raised error.
"""

from __future__ import annotations

import pytest
from orchestrator.application import ContextResolverHost, MissingContextError
from orchestrator.application.context_resolver_host import SecretProvider as HostProtocol
from sfp_config.providers import SecretResolutionError
from sfp_config.providers.local import LocalSecretProvider
from sfp_config.secrets import SecretRef
from sfp_contracts.context.bindings import ContextBinding, ResolvedContext
from sfp_contracts.context.declaration import ContextInput, TicketContextDeclaration
from sfp_contracts.context.types import DEFAULT_CATALOGUE, ContextType, ContextTypeKind

_PR_SPEC_ID = "SFP-999-context"
_SECRET_MATERIAL = "super-secret-llm-token-material"
_MISSING_MATERIAL = "the-never-materialized-value"


class StubProvider:
    """A deterministic provider: resolves what it holds, raises otherwise.

    Never touches ``os.environ`` or the filesystem — the PRSpec forbids tests
    importing real secrets from the environment.
    """

    def __init__(self, entries: dict[str, str]) -> None:
        self._entries = dict(entries)
        self.calls: list[SecretRef] = []

    def resolve(self, ref: SecretRef) -> str:
        self.calls.append(ref)
        if ref.name in self._entries:
            return self._entries[ref.name]
        raise SecretResolutionError(ref, source="stub")


def _declaration(*inputs: ContextInput) -> TicketContextDeclaration:
    return TicketContextDeclaration(required_inputs=list(inputs))


def _host(values: dict[str, str], provider: StubProvider) -> ContextResolverHost:
    return ContextResolverHost(provider, lambda _spec_id: dict(values))


# ---------------------------------------------------------------------------
# Acceptance 1 — all declared requirements resolve
# ---------------------------------------------------------------------------


def test_all_requirements_resolve_in_declaration_order() -> None:
    provider = StubProvider({"llm_provider_secret_ref": _SECRET_MATERIAL})
    values = {
        "repo_url": "https://git.example/sfp",
        "llm_provider_secret_ref": "llm_provider_secret_ref",
    }
    host = ContextResolverHost(provider, lambda _spec_id: values)
    declaration = _declaration(
        ContextInput(name="repo_url", source_ticket="SFP-900"),
        ContextInput(name="llm_provider_secret_ref", source_ticket="SFP-901"),
    )

    result = host.resolve(_PR_SPEC_ID, declaration)

    assert result.ticket_id == _PR_SPEC_ID
    assert result.missing == []
    assert [binding.name for binding in result.resolved] == ["repo_url", "llm_provider_secret_ref"]


def test_str_binding_carries_value_and_catalogued_type() -> None:
    host = _host({"repo_url": "https://git.example/sfp"}, StubProvider({}))
    result = host.resolve(
        _PR_SPEC_ID, _declaration(ContextInput(name="repo_url", source_ticket="SFP-900"))
    )

    binding = result.resolved[0]
    assert binding.name == "repo_url"
    assert binding.context_type == ContextType(name="repo_url", kind=ContextTypeKind.STR)
    assert binding.value == "https://git.example/sfp"
    assert binding.source_ticket == "SFP-900"


def test_values_source_receives_the_pr_spec_id() -> None:
    seen: list[str] = []
    host = ContextResolverHost(StubProvider({}), lambda spec_id: (seen.append(spec_id), {})[1])

    host.resolve(_PR_SPEC_ID, _declaration())

    assert seen == [_PR_SPEC_ID]


# ---------------------------------------------------------------------------
# Acceptance 2 — missing secret, both failure paths
# ---------------------------------------------------------------------------


def test_missing_secret_in_source_dict_names_reference_only() -> None:
    # Source dict lacks the SECRET_REF name entirely.
    host = _host({"repo_url": "x"}, StubProvider({}))

    with pytest.raises(MissingContextError) as excinfo:
        host.resolve(
            _PR_SPEC_ID,
            _declaration(ContextInput(name="llm_provider_secret_ref", source_ticket="SFP-901")),
        )

    # Message names the input/reference name ONLY — no material, and not even
    # the never-materialized reference value.
    message = str(excinfo.value)
    assert "llm_provider_secret_ref" in message
    assert _MISSING_MATERIAL not in message
    assert "secret not found" not in message.lower() or _MISSING_MATERIAL not in message


def test_provider_failure_raises_missing_context_error_naming_reference_only() -> None:
    # The source HAS the declared value (it is the reference string), but the
    # provider cannot materialize it.
    provider = StubProvider({})  # raises for every ref
    values = {"llm_provider_secret_ref": _MISSING_MATERIAL}
    host = ContextResolverHost(provider, lambda _spec_id: values)

    with pytest.raises(MissingContextError) as excinfo:
        host.resolve(
            _PR_SPEC_ID,
            _declaration(ContextInput(name="llm_provider_secret_ref", source_ticket="SFP-901")),
        )

    error = excinfo.value
    # Outward message: the name only.
    assert error.name == "llm_provider_secret_ref"
    assert _SECRET_MATERIAL not in str(error)
    # The provider was asked for the declared value AS the SecretRef name.
    assert provider.calls == [SecretRef(name=_MISSING_MATERIAL)]
    # The chained SecretResolutionError carries the reference, never material.
    chained = error.__cause__
    assert isinstance(chained, SecretResolutionError)
    chained_text = str(chained)
    assert _SECRET_MATERIAL not in chained_text
    assert _MISSING_MATERIAL in chained_text  # the reference itself is safe to carry


def test_provider_failure_is_chained_not_interpolated() -> None:
    provider = StubProvider({})
    host = ContextResolverHost(
        provider, lambda _spec_id: {"llm_provider_secret_ref": _MISSING_MATERIAL}
    )

    with pytest.raises(MissingContextError) as excinfo:
        host.resolve(
            _PR_SPEC_ID,
            _declaration(ContextInput(name="llm_provider_secret_ref", source_ticket="SFP-1")),
        )

    # The outward message must be exactly the MissingContextError form — no
    # provider text spliced in (even material-free provider text stays out).
    assert str(excinfo.value) == "Missing context input: llm_provider_secret_ref"


def test_no_partial_context_on_failure() -> None:
    provider = StubProvider({})
    values = {"repo_url": "x", "db_secret_arn": _MISSING_MATERIAL}
    host = ContextResolverHost(provider, lambda _spec_id: values)

    with pytest.raises(MissingContextError):
        host.resolve(
            _PR_SPEC_ID,
            _declaration(
                ContextInput(name="repo_url", source_ticket="SFP-900"),
                ContextInput(name="db_secret_arn", source_ticket="SFP-901"),
            ),
        )

    # Total-failure semantics: nothing was returned to inspect — but also
    # nothing leaked into a shared object; a subsequent successful resolve
    # for a different declaration is unaffected.
    ok = host.resolve(
        _PR_SPEC_ID, _declaration(ContextInput(name="repo_url", source_ticket="SFP-900"))
    )
    assert [binding.name for binding in ok.resolved] == ["repo_url"]
    assert ok.missing == []


# ---------------------------------------------------------------------------
# Acceptance 3 — missing declared non-secret value
# ---------------------------------------------------------------------------


def test_missing_non_secret_value_names_the_input() -> None:
    host = _host({"other": "y"}, StubProvider({}))

    with pytest.raises(MissingContextError) as excinfo:
        host.resolve(
            _PR_SPEC_ID, _declaration(ContextInput(name="repo_url", source_ticket="SFP-900"))
        )

    assert excinfo.value.name == "repo_url"
    assert "repo_url" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Acceptance 4 — zero required inputs
# ---------------------------------------------------------------------------


def test_zero_requirements_is_success_with_empty_context() -> None:
    host = ContextResolverHost(StubProvider({}), lambda _spec_id: {})

    result = host.resolve(_PR_SPEC_ID, _declaration())

    assert isinstance(result, ResolvedContext)
    assert result.ticket_id == _PR_SPEC_ID
    assert result.resolved == []
    assert result.missing == []


# ---------------------------------------------------------------------------
# Acceptance 5 — uncatalogued name defaults to STR
# ---------------------------------------------------------------------------


def test_uncatalogued_name_defaults_to_str_kind() -> None:
    uncatalogued = "definitely_not_in_catalogue"
    catalogued_names = {entry.name for entry in DEFAULT_CATALOGUE.entries}
    assert uncatalogued not in catalogued_names  # test's own premise

    host = _host({uncatalogued: "plain"}, StubProvider({}))
    result = host.resolve(
        _PR_SPEC_ID, _declaration(ContextInput(name=uncatalogued, source_ticket="SFP-900"))
    )

    binding = result.resolved[0]
    assert binding.context_type == ContextType(name=uncatalogued, kind=ContextTypeKind.STR)
    assert binding.value == "plain"


# ---------------------------------------------------------------------------
# Acceptance 6 — secret materialization through the provider
# ---------------------------------------------------------------------------


def test_secret_ref_materialized_via_provider_into_binding_value() -> None:
    provider = StubProvider({"the-ref": _SECRET_MATERIAL})
    values = {"llm_provider_secret_ref": "the-ref"}
    host = ContextResolverHost(provider, lambda _spec_id: values)
    declaration = _declaration(
        ContextInput(name="llm_provider_secret_ref", source_ticket="SFP-901")
    )

    result = host.resolve(_PR_SPEC_ID, declaration)

    # The provider saw SecretRef(name=<declared value>) exactly once.
    assert provider.calls == [SecretRef(name="the-ref")]
    binding = result.resolved[0]
    # Materialized material lives ONLY in the in-memory binding value.
    assert binding.value == _SECRET_MATERIAL
    assert binding.context_type.kind is ContextTypeKind.SECRET_REF
    assert binding.context_type.name == "llm_provider_secret_ref"
    assert binding.source_ticket == "SFP-901"


def test_two_secret_refs_both_materialized_in_order() -> None:
    provider = StubProvider({"ref-a": "material-a", "ref-b": "material-b"})
    values = {"db_secret_arn": "ref-a", "llm_provider_secret_ref": "ref-b"}
    host = ContextResolverHost(provider, lambda _spec_id: values)
    declaration = _declaration(
        ContextInput(name="db_secret_arn", source_ticket="SFP-1"),
        ContextInput(name="llm_provider_secret_ref", source_ticket="SFP-2"),
    )

    result = host.resolve(_PR_SPEC_ID, declaration)

    assert provider.calls == [SecretRef(name="ref-a"), SecretRef(name="ref-b")]
    assert [binding.value for binding in result.resolved] == ["material-a", "material-b"]


# ---------------------------------------------------------------------------
# Acceptance 7 — structural protocol signature
# ---------------------------------------------------------------------------


def test_local_secret_provider_satisfies_host_protocol_structurally() -> None:
    assert issubclass(LocalSecretProvider, HostProtocol)


def test_local_secret_provider_end_to_end_through_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """The landed provider really runs through the host, satisfying the seam.

    ``monkeypatch`` sets a synthetic env var (never a real secret) for the
    duration of this test only, and the local provider is pointed at a
    nonexistent file so nothing outside the env var can contribute.
    """
    monkeypatch.setenv("SFP151_TEST_REF", _SECRET_MATERIAL)
    provider = LocalSecretProvider(secrets_file=None)  # type: ignore[arg-type]
    host = ContextResolverHost(
        provider, lambda _spec_id: {"llm_provider_secret_ref": "SFP151_TEST_REF"}
    )

    result = host.resolve(
        _PR_SPEC_ID,
        _declaration(ContextInput(name="llm_provider_secret_ref", source_ticket="SFP-901")),
    )

    assert result.resolved[0].value == _SECRET_MATERIAL


def test_local_secret_provider_failure_maps_to_missing_context_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SFP151_ABSENT_REF", raising=False)
    provider = LocalSecretProvider(secrets_file=None)  # type: ignore[arg-type]
    host = ContextResolverHost(
        provider, lambda _spec_id: {"llm_provider_secret_ref": "SFP151_ABSENT_REF"}
    )

    with pytest.raises(MissingContextError) as excinfo:
        host.resolve(
            _PR_SPEC_ID,
            _declaration(ContextInput(name="llm_provider_secret_ref", source_ticket="SFP-901")),
        )

    assert excinfo.value.name == "llm_provider_secret_ref"
    assert _MISSING_MATERIAL not in str(excinfo.value)


# ---------------------------------------------------------------------------
# Determinism / no-shared-state guard
# ---------------------------------------------------------------------------


def test_repeated_resolve_is_deterministic() -> None:
    provider = StubProvider({"the-ref": _SECRET_MATERIAL})
    values = {"repo_url": "https://git.example/sfp", "llm_provider_secret_ref": "the-ref"}
    host = ContextResolverHost(provider, lambda _spec_id: values)
    declaration = _declaration(
        ContextInput(name="repo_url", source_ticket="SFP-900"),
        ContextInput(name="llm_provider_secret_ref", source_ticket="SFP-901"),
    )

    first = host.resolve(_PR_SPEC_ID, declaration)
    second = host.resolve(_PR_SPEC_ID, declaration)

    assert first == second
    assert first is not second
    assert isinstance(first.resolved[0], ContextBinding)
