"""Interfaces layer for the External Events service.

Inbound/outbound adapters at the service boundary. Resident: the SFP-120
single external webhook ingress — :class:`WebhookIngressEndpoint`, the ASGI
application serving ``POST /webhooks/{endpoint_id}`` that composes the
SFP-121 resolver, the SFP-122 factory + SFP-123 strategies, and the SFP-124
publisher into one ingress with transport-level-only concerns.
"""

from __future__ import annotations

from external_events.interfaces.webhook import (
    WEBHOOK_PATH_PREFIX,
    WebhookIngressEndpoint,
)

__all__ = [
    "WEBHOOK_PATH_PREFIX",
    "WebhookIngressEndpoint",
]
