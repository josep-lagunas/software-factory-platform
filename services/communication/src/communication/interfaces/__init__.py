"""Communication-service interface adapters (MAS §9.4).

The ``interfaces/`` layer hosts the provider-facing edges of the service
(ID-051, AP-007):

- :mod:`communication.interfaces.outbound` — the outbound port:
  ``OutboundMessagePort`` (abstract), ``DeliveryReceipt``, ``ProviderError``.
- :mod:`communication.interfaces.slack_outbound` — the v0 Slack
  implementation (ID-027): ``SlackOutboundClient`` via ``chat.postMessage``.
- :mod:`communication.interfaces.slack_inbound` — the v0 Slack inbound
  consumer (SFP-132): the local Slack provider schema plus the
  registry-dispatched ``ExternalEventReceived(source="slack")`` handler that
  advances the ``UserInteraction`` and publishes
  ``UserQueryReceived`` / ``UserInputReceived`` (ID-076). Ingress HTTP
  belongs to the External Events Service (MAS §9.2) — Communication consumes
  off the bus only.

Future providers (email, …) implement the same port without touching callers.
"""

from communication.interfaces.outbound import (
    DeliveryReceipt,
    OutboundMessagePort,
    ProviderError,
)
from communication.interfaces.slack_inbound import (
    SLACK_SOURCE,
    SlackInboundConsumer,
    SlackProviderMessage,
    handle_external_event_received,
    parse_slack_message,
    set_slack_inbound_consumer,
)
from communication.interfaces.slack_outbound import SlackOutboundClient

__all__ = [
    "DeliveryReceipt",
    "OutboundMessagePort",
    "ProviderError",
    "SLACK_SOURCE",
    "SlackInboundConsumer",
    "SlackOutboundClient",
    "SlackProviderMessage",
    "handle_external_event_received",
    "parse_slack_message",
    "set_slack_inbound_consumer",
]
