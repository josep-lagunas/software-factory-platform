"""Tests for the SFP-122 auth-strategy Protocol + factory.

Fully hermetic and deterministic: selection is exercised through a
test-injected registry, and the SFP-86 ``SecretProvider`` seam is a
recording fake, so no environment, no network, no clock touches anything.
The module registry itself now ships the two SFP-123 v0 strategies
(populated on package import by
``external_events.application.strategies``) — asserted below.

Covers the acceptance criteria: per-key class selection from the registry,
the secret VALUE resolved by the injected provider and injected at
construction (never the reference — ID-029), the typed
``UnknownAuthStrategyError`` carrying the offending key (and raised before
any secret is pulled), the pure ``authenticate`` decision over
``raw_body``/``headers``, and the ``application`` package exports.
"""

from __future__ import annotations

from collections.abc import Mapping

import pytest
from external_events.application import (
    AUTH_STRATEGY_REGISTRY,
    AuthenticationStrategy,
    UnknownAuthStrategyError,
    build_authentication_strategy,
)
from external_events.application import auth_factory as auth_factory_module
from external_events.application.auth_factory import (
    AUTH_STRATEGY_REGISTRY as MODULE_REGISTRY,
)
from external_events.application.auth_factory import (
    build_authentication_strategy as MODULE_BUILD,
)
from external_events.application.strategies import (
    GitHubHmacStrategy,
    SlackSignatureStrategy,
)
from sfp_config.providers import SecretResolutionError
from sfp_config.secrets import SecretRef


class _RecordingProvider:
    """Structural ``SecretProvider``: records every ref, returns a canned value."""

    def __init__(self, value: str = "resolved-secret-value") -> None:
        self.value = value
        self.calls: list[SecretRef] = []

    def resolve(self, ref: SecretRef) -> str:
        self.calls.append(ref)
        return self.value


class _RaisingProvider:
    """Structural ``SecretProvider`` that always fails resolution."""

    def resolve(self, ref: SecretRef) -> str:
        raise SecretResolutionError(ref, source="test")


class _HmacStrategy:
    """Dummy strategy: verdict is a pure mapping of its two inputs."""

    def __init__(self, secret_value: str) -> None:
        self.secret_value = secret_value

    def authenticate(self, raw_body: bytes, headers: Mapping[str, str]) -> bool:
        return raw_body == b"legit-body" and headers.get("x-signature") == "valid"


class _SharedSecretStrategy:
    """A second, distinct strategy class to prove per-key selection."""

    def __init__(self, secret_value: str) -> None:
        self.secret_value = secret_value

    def authenticate(self, raw_body: bytes, headers: Mapping[str, str]) -> bool:
        return raw_body == b"other" and headers.get("authorization") == "Bearer ok"


#: A test registry — the same shape the module registry now carries (SFP-123).
_TEST_REGISTRY = {
    "hmac_sha256": _HmacStrategy,
    "shared_secret": _SharedSecretStrategy,
}


# --- selection ----------------------------------------------------------------


def test_selects_the_strategy_class_registered_under_the_key() -> None:
    """Each key selects exactly its own registered class."""
    provider = _RecordingProvider()

    hmac_result = build_authentication_strategy(
        "hmac_sha256", "op://prod/github/webhook", provider, registry=_TEST_REGISTRY
    )
    shared_result = build_authentication_strategy(
        "shared_secret", "op://prod/slack/webhook", provider, registry=_TEST_REGISTRY
    )

    assert isinstance(hmac_result, _HmacStrategy)
    assert isinstance(shared_result, _SharedSecretStrategy)


def test_builds_a_fresh_instance_per_call() -> None:
    """The factory constructs classes per build — no shared instances."""
    provider = _RecordingProvider()

    first = build_authentication_strategy(
        "hmac_sha256", "op://prod/github/webhook", provider, registry=_TEST_REGISTRY
    )
    second = build_authentication_strategy(
        "hmac_sha256", "op://prod/github/webhook", provider, registry=_TEST_REGISTRY
    )

    assert first is not second


# --- secret injection (ID-029: value at construction, never self-loaded) -----


def test_injects_the_resolved_secret_value_at_construction() -> None:
    """The constructor receives the provider's VALUE, not the reference."""
    provider = _RecordingProvider(value="the-actual-secret")

    result = build_authentication_strategy(
        "hmac_sha256", "op://prod/github/webhook", provider, registry=_TEST_REGISTRY
    )

    assert result.secret_value == "the-actual-secret"
    # The opaque reference string must never reach the strategy (ID-016/ID-029).
    assert result.secret_value != "op://prod/github/webhook"


def test_delegates_resolution_to_the_injected_secret_provider() -> None:
    """The ref string reaches the provider as ``SecretRef(name=...)`` exactly once."""
    provider = _RecordingProvider()

    build_authentication_strategy(
        "hmac_sha256", "op://prod/github/webhook", provider, registry=_TEST_REGISTRY
    )

    assert provider.calls == [SecretRef(name="op://prod/github/webhook")]
    assert provider.calls[0].version is None  # unversioned → "current"


def test_provider_resolution_failure_propagates_verbatim() -> None:
    """The factory neither swallows nor rewraps SecretResolutionError (SFP-86's concern)."""
    with pytest.raises(SecretResolutionError):
        build_authentication_strategy(
            "hmac_sha256", "op://nowhere/key", _RaisingProvider(), registry=_TEST_REGISTRY
        )


# --- unknown-key error path ----------------------------------------------------


def test_unknown_key_raises_typed_error_carrying_the_key() -> None:
    """Registry miss → UnknownAuthStrategyError with the offending key as payload."""
    provider = _RecordingProvider()

    with pytest.raises(UnknownAuthStrategyError) as exc_info:
        build_authentication_strategy(
            "bearer_token", "op://prod/x/y", provider, registry=_TEST_REGISTRY
        )

    assert exc_info.value.auth_strategy == "bearer_token"
    # Discipline mirror of EndpointConfigNotFoundError: a LookupError subtype,
    # never a bare KeyError callers might catch by accident.
    assert isinstance(exc_info.value, LookupError)
    assert not isinstance(exc_info.value, KeyError)


def test_unknown_key_fails_fast_before_any_secret_resolution() -> None:
    """An unknown key must never pull a secret from the provider."""
    provider = _RecordingProvider()

    with pytest.raises(UnknownAuthStrategyError):
        build_authentication_strategy(
            "nonexistent", "op://prod/x/y", provider, registry=_TEST_REGISTRY
        )

    assert provider.calls == []


# --- registry wiring -----------------------------------------------------------


def test_v0_registry_holds_exactly_the_sfp_123_strategies() -> None:
    """SFP-123 populated the v0 registry: exactly the two v0 strategies,
    under the exact ``auth_strategy`` keys of the SFP-113 endpoint model."""
    assert AUTH_STRATEGY_REGISTRY == {
        "slack_signature": SlackSignatureStrategy,
        "github_hmac": GitHubHmacStrategy,
    }


def test_default_registry_is_the_module_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Calling without ``registry`` selects from the module-level registry."""
    monkeypatch.setitem(AUTH_STRATEGY_REGISTRY, "hmac_sha256", _HmacStrategy)

    result = build_authentication_strategy(
        "hmac_sha256", "op://prod/github/webhook", _RecordingProvider()
    )

    assert isinstance(result, _HmacStrategy)


# --- protocol shape ------------------------------------------------------------


def test_authenticate_is_a_pure_decision_over_body_and_headers() -> None:
    """The verdict depends only on (raw_body, headers) — no HTTP objects, no I/O."""
    strategy = build_authentication_strategy(
        "hmac_sha256", "op://prod/github/webhook", _RecordingProvider(), registry=_TEST_REGISTRY
    )

    assert strategy.authenticate(b"legit-body", {"x-signature": "valid"}) is True
    assert strategy.authenticate(b"tampered", {"x-signature": "valid"}) is False
    assert strategy.authenticate(b"legit-body", {"x-signature": "bad"}) is False
    assert strategy.authenticate(b"legit-body", {}) is False


# --- package exports -----------------------------------------------------------


def test_package_exports_the_contract_names() -> None:
    """SFP-123/SFP-120 import from external_events.application without a cycle."""
    assert auth_factory_module.AuthenticationStrategy is AuthenticationStrategy
    assert auth_factory_module.UnknownAuthStrategyError is UnknownAuthStrategyError
    assert MODULE_BUILD is build_authentication_strategy
    assert MODULE_REGISTRY is AUTH_STRATEGY_REGISTRY
