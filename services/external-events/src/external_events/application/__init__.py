"""Application layer for the External Events service.

Use-case orchestration over the service's domain and persistence layers.
The first resident is the SFP-121 endpoint-configuration resolver; webhook
ingress (SFP-120) and downstream authentication (SFP-122) consume it.
"""

from __future__ import annotations

from external_events.application.endpoint_resolver import (
    EndpointConfigNotFoundError,
    EndpointConfigResolver,
    ResolvedEndpointConfig,
    SessionFactory,
    resolve,
)

__all__ = [
    "EndpointConfigNotFoundError",
    "EndpointConfigResolver",
    "ResolvedEndpointConfig",
    "SessionFactory",
    "resolve",
]
