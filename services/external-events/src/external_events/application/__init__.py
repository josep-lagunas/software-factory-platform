"""Application layer for the External Events service.

Use-case orchestration over the service's domain and persistence layers.
Residents: the SFP-121 endpoint-configuration resolver and the SFP-122
authentication-strategy Protocol + factory; webhook ingress (SFP-120) and
the concrete strategies (SFP-123) consume both.
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

__all__ = [
    "AUTH_STRATEGY_REGISTRY",
    "AuthenticationStrategy",
    "EndpointConfigNotFoundError",
    "EndpointConfigResolver",
    "ResolvedEndpointConfig",
    "SessionFactory",
    "StrategyRegistry",
    "UnknownAuthStrategyError",
    "build_authentication_strategy",
    "resolve",
]
