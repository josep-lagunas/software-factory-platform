"""Entrypoints of the communication service (the inbound/outbound HTTP edge).

- :mod:`communication.entrypoints.slack_events_endpoint` — the authenticated
  Slack Events API receiver (SFP-132): v0 signature verification, replay
  protection, and ``ExternalEventReceived`` publication onto the MessageBus.
- :mod:`communication.entrypoints.dev_slack_events` — the local dev runner
  serving the receiver for tunnel-based dogfooding (no AWS).
"""

from communication.entrypoints.dev_slack_events import (
    DEFAULT_PORT,
    build_dev_app,
)
from communication.entrypoints.slack_events_endpoint import (
    SLACK_EVENTS_PATH,
    SLACK_SIGNING_SECRET_REF,
    EventEnvelopeFactory,
    SlackEventsEndpoint,
    make_external_event_envelope,
)

__all__ = [
    "DEFAULT_PORT",
    "EventEnvelopeFactory",
    "SLACK_EVENTS_PATH",
    "SLACK_SIGNING_SECRET_REF",
    "SlackEventsEndpoint",
    "build_dev_app",
    "make_external_event_envelope",
]
