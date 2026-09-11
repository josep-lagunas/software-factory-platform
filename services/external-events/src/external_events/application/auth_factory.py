"""Application-layer auth-strategy Protocol and factory (SFP-122).

Maps an endpoint's ``auth_strategy`` key (from the SFP-121 resolver) to an
:class:`AuthenticationStrategy` instance, with the endpoint's ``secret_ref``
resolved to its **value** by the injected SFP-86
:class:`~sfp_config.providers.SecretProvider` *before* construction —
strategies never load secrets themselves (ID-029: the decision is pure; the
secret arrives as data, at construction, from the outside).

Scope fences (what this module deliberately does NOT do):

- **No concrete strategies.** The v0 registry :data:`AUTH_STRATEGY_REGISTRY`
  ships **empty** by design — SFP-123 implements the strategies and populates
  it. The factory's contract is fully testable against a test-injected
  registry, so the empty registry is not a gap.
- **No constant-time comparison.** That discipline belongs to the strategy
  implementations (SFP-123), never to this factory.
- **No secret storage/resolution.** Resolution is delegated wholesale to the
  injected :class:`~sfp_config.providers.SecretProvider` (SFP-86 seam);
  a reference the provider cannot resolve propagates the provider's
  :class:`~sfp_config.providers.SecretResolutionError` untouched.
- **No transport.** No HTTP request/response objects cross this boundary —
  ``authenticate`` reasons over ``raw_body``/``headers`` data only — and no
  SNS/SQS concerns exist here (SFP-118, Phase-B deferred).

Error contract (binding, consumed by SFP-120): an ``auth_strategy`` key the
registry does not know raises :class:`UnknownAuthStrategyError` carrying the
offending key as the assertable attribute ``auth_strategy`` — a typed
exception, never a ``None`` return and never a bare ``KeyError`` (discipline
mirror of SFP-121's ``EndpointConfigNotFoundError``). The lookup happens
**before** any secret resolution, so an unknown key never pulls a secret.

Determinism (MAS §12.7): no clock, no network beyond the injected provider,
no randomness — the same registry, key, ref and provider yield the same
strategy every time.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from sfp_config.providers import SecretProvider
from sfp_config.secrets import SecretRef

__all__ = [
    "AUTH_STRATEGY_REGISTRY",
    "AuthenticationStrategy",
    "StrategyRegistry",
    "UnknownAuthStrategyError",
    "build_authentication_strategy",
]


class AuthenticationStrategy(Protocol):
    """One endpoint's webhook-authentication decision.

    A strategy is a **pure decision** over the two ingress inputs: given the
    raw request body bytes and the request headers, decide accept (``True``)
    or reject (``False``). No HTTP framework types, no I/O, no secret
    loading — the secret arrives as constructor data (ID-029), and the
    constant-time comparison discipline lives in the implementations
    (SFP-123), not in this contract.

    Construction contract (binding for registry classes, and part of this
    Protocol): a strategy class takes exactly one argument — the endpoint's
    secret **value**, already resolved. That is what lets the factory own
    resolution (via the injected provider) while the strategy stays pure.
    """

    def __init__(self, secret_value: str) -> None:
        """Construct with the resolved secret VALUE (never a reference).

        Args:
            secret_value: The endpoint's secret, resolved by the factory via
                the injected :class:`~sfp_config.providers.SecretProvider`.
                Strategies never resolve secrets themselves (ID-029).
        """
        ...

    def authenticate(self, raw_body: bytes, headers: Mapping[str, str]) -> bool:
        """Decide whether this request authenticates.

        Args:
            raw_body: The raw request body bytes (the exact bytes the
                signature was computed over — re-serialization is a bug).
            headers: The request headers, lowercase-keyed mapping of str to
                str. Read-only input; the strategy mutates nothing.

        Returns:
            ``True`` to accept the request, ``False`` to reject. Never
            raises for a decision outcome — an auth decision is a verdict,
            not an error.
        """
        ...


#: The registry shape: auth-strategy key -> strategy class.
StrategyRegistry = dict[str, type[AuthenticationStrategy]]

#: Registry of strategy classes keyed by the ``auth_strategy`` string of the
#: SFP-113 endpoint model — keyed by *strategy*, not by provider (SFP-113:
#: providers share strategies). Values are classes (constructed per build),
#: not instances. v0 ships EMPTY by Planner decision: SFP-123 populates it;
#: until then the factory is exercised through a test-injected registry.
AUTH_STRATEGY_REGISTRY: StrategyRegistry = {}


class UnknownAuthStrategyError(LookupError):
    """Raised when the registry holds no class for an ``auth_strategy`` key.

    The endpoint configuration names a strategy the factory cannot select.
    Subclasses :class:`LookupError` (a registry lookup missed), *not*
    :class:`KeyError` — callers must single out this type, never catch a
    generic mapping error (discipline mirror of SFP-121's
    ``EndpointConfigNotFoundError``).

    The carried ``auth_strategy`` attribute is a binding contract: SFP-120
    maps this exception to an authentication-configuration failure and
    asserts/logs the offending key from the exception payload.
    """

    def __init__(self, auth_strategy: str) -> None:
        super().__init__(f"Unknown auth strategy: {auth_strategy!r}")
        self.auth_strategy = auth_strategy


def build_authentication_strategy(
    auth_strategy: str,
    secret_ref: str,
    secret_provider: SecretProvider,
    *,
    registry: StrategyRegistry = AUTH_STRATEGY_REGISTRY,
) -> AuthenticationStrategy:
    """Select the strategy class for ``auth_strategy`` and build it.

    Selection order (each step deterministic, fail-fast):

    1. **Select** the class from ``registry`` by the ``auth_strategy`` key.
       A miss raises :class:`UnknownAuthStrategyError` **before any secret
       is resolved** — an unknown key never pulls a secret.
    2. **Resolve** ``secret_ref`` to its value via the injected
       ``secret_provider``: the opaque reference string from the SFP-121
       resolver becomes ``SecretRef(name=secret_ref)`` (``version=None`` →
       "current"). Strategies never load secrets themselves (ID-029); a
       reference the provider cannot resolve propagates the provider's
       :class:`~sfp_config.providers.SecretResolutionError` untouched.
    3. **Construct** the selected class with the resolved VALUE.

    Args:
        auth_strategy: The strategy key from the endpoint's configuration
            (SFP-121 ``ResolvedEndpointConfig.auth_strategy``).
        secret_ref: The endpoint's opaque secret reference string (SFP-121
            ``ResolvedEndpointConfig.secret_ref``) — a reference, never a
            value (ID-016).
        secret_provider: The injected SFP-86 seam that turns ``secret_ref``
            into the secret value.
        registry: The key→class registry to select from. Defaults to the
            module-level :data:`AUTH_STRATEGY_REGISTRY` (which SFP-123
            populates); tests inject their own.

    Returns:
        A ready-to-authenticate strategy holding the resolved secret value.

    Raises:
        UnknownAuthStrategyError: ``registry`` holds no entry for
            ``auth_strategy``; raised without consulting
            ``secret_provider``.
        SecretResolutionError: The provider could not resolve
            ``secret_ref`` (propagated verbatim — resolution is SFP-86's
            concern, not this factory's).
    """
    strategy_cls = registry.get(auth_strategy)
    if strategy_cls is None:
        # Fail fast, before secret resolution: an unknown key must never
        # pull a secret. Typed exception carrying the offending key.
        raise UnknownAuthStrategyError(auth_strategy)

    secret_value = secret_provider.resolve(SecretRef(name=secret_ref))
    return strategy_cls(secret_value)
