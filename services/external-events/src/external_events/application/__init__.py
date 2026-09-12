"""Application layer for the External Events service.

Use-case orchestration over the service's domain and persistence layers.
Residents: the SFP-121 endpoint-configuration resolver, the SFP-122
authentication-strategy Protocol + factory, and the concrete strategies
(SFP-123) — imported below so their registration into
:data:`AUTH_STRATEGY_REGISTRY` happens on any import of this package;
webhook ingress (SFP-120) consumes all three.
"""

from __future__ import annotations

from external_events.application.auth_factory import (
    AUTH_STRATEGY_REGISTRY,
    AuthenticationStrategy,
    StrategyRegistry,
    UnknownAuthStrategyError,
    build_authentication_strategy,
)
from external_events.application.endpoint_resolver import (
    EndpointConfigNotFoundError,
    EndpointConfigResolver,
    ResolvedEndpointConfig,
    SessionFactory,
    resolve,
)

# SFP-123: importing the strategies package populates AUTH_STRATEGY_REGISTRY
# with the v0 strategies (registration is a package-import side effect of
# external_events.application.strategies; it must come after auth_factory
# so the registry exists to populate — hence this ordering, not alphabetical).
from external_events.application.strategies import (
    GitHubHmacStrategy,
    SlackSignatureStrategy,
)

__all__ = [
    "AUTH_STRATEGY_REGISTRY",
    "AuthenticationStrategy",
    "EndpointConfigNotFoundError",
    "EndpointConfigResolver",
    "GitHubHmacStrategy",
    "ResolvedEndpointConfig",
    "SessionFactory",
    "SlackSignatureStrategy",
    "StrategyRegistry",
    "UnknownAuthStrategyError",
    "build_authentication_strategy",
    "resolve",
]
