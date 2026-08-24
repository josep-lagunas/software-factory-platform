"""Communication-service interface adapters (MAS §9.4).

The ``interfaces/`` layer hosts the provider-facing edges of the service. The
outbound-message seam is provider-agnostic by design (ID-051, AP-007):

- :mod:`communication.interfaces.outbound` — the port:
  ``OutboundMessagePort`` (abstract), ``DeliveryReceipt``, ``ProviderError``.
- :mod:`communication.interfaces.slack_outbound` — the v0 Slack
  implementation (ID-027): ``SlackOutboundClient`` via ``chat.postMessage``.

Future providers (email, …) implement the same port without touching callers.
"""

from communication.interfaces.outbound import (
    DeliveryReceipt,
    OutboundMessagePort,
    ProviderError,
)
from communication.interfaces.slack_outbound import SlackOutboundClient

__all__ = [
    "DeliveryReceipt",
    "OutboundMessagePort",
    "ProviderError",
    "SlackOutboundClient",
]
