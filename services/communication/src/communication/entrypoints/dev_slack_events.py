"""Local dev runner for the Slack Events receiver (SFP-132).

Serves :class:`~communication.entrypoints.slack_events_endpoint.SlackEventsEndpoint`
on a configurable port (default 8788) for tunnel-based dogfooding — point
cloudflared/ngrok at it, then set the Slack app's Event Subscriptions request
URL to the tunnel's ``/slack/events`` path. No AWS, no cloud: the v0 hosting
path is exactly this runner plus a tunnel (production hosting is the hosting
epic, deferred by design).

Wiring (all in-process, per the software-first owner decision):
- ``LocalSecretProvider`` resolves ``SLACK_SIGNING_SECRET`` from the
  environment or ``secrets.local`` (SFP-86).
- :class:`~sfp_messaging.transport.in_memory.InMemoryTransport` is the bus —
  published events land on ``bus.published_messages`` (downstream
  interpretation, SFP-244, subscribes separately).
- :func:`~communication.entrypoints.slack_events_endpoint.make_external_event_envelope`
  supplies envelope identity (deterministic ``idempotency_key`` from the
  Slack ``ts``).

Usage::

    uv run python -m communication.entrypoints.dev_slack_events [--port 8788]

    # or via env (env wins only when --port is absent):
    SLACK_EVENTS_PORT=9000 uv run python -m communication.entrypoints.dev_slack_events

Prerequisite (HUMAN ACTION, ~10 min, owner): enable Event Subscriptions on
the Slack app (``app_mentions:read`` + message events for the ops channel)
and point the request URL at the tunnel — the existing SFP-86 scopes
suffice.
"""

from __future__ import annotations

import argparse
import logging
import os
from typing import Final

from sfp_config import LocalSecretProvider
from sfp_messaging.transport.in_memory import InMemoryTransport

from communication.entrypoints.slack_events_endpoint import (
    SLACK_EVENTS_PATH,
    SlackEventsEndpoint,
    make_external_event_envelope,
)

__all__ = [
    "DEFAULT_PORT",
    "PORT_ENV_VAR",
    "build_dev_app",
]

#: Default listen port (SFP-132).
DEFAULT_PORT: Final[int] = 8788

#: Environment variable overriding the port (used when ``--port`` is absent).
PORT_ENV_VAR: Final[str] = "SLACK_EVENTS_PORT"

logger = logging.getLogger(__name__)


def build_dev_app() -> tuple[SlackEventsEndpoint, InMemoryTransport]:
    """Wire the endpoint with in-memory bus + local secrets (dev composition).

    Returns the app and the bus so the runner (and tests) can inspect what
    was published via ``bus.published_messages``.
    """
    bus = InMemoryTransport()
    app = SlackEventsEndpoint(
        bus,
        LocalSecretProvider(),
        envelope_factory=make_external_event_envelope,
    )
    return app, bus


def _resolve_port(cli_port: int | None) -> int:
    """Resolve the listen port: ``--port`` wins, then env, then default."""
    if cli_port is not None:
        return cli_port
    raw = os.environ.get(PORT_ENV_VAR, "")
    if raw.strip().isdigit():
        return int(raw)
    return DEFAULT_PORT


def main(argv: list[str] | None = None) -> None:
    """Parse args and serve the endpoint until interrupted."""
    parser = argparse.ArgumentParser(
        prog="dev_slack_events",
        description=(
            "Serve the Slack Events receiver locally (SFP-132) for tunnel-based dogfooding."
        ),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help=f"Listen port (default {DEFAULT_PORT}, env {PORT_ENV_VAR}).",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    app, _bus = build_dev_app()
    port = _resolve_port(args.port)

    # Imported here (not module-level) so importing this module for its
    # wiring helpers never drags the server in — tests build the app without
    # binding a socket.
    import uvicorn

    logger.info(
        "SFP-132 dev Slack Events receiver: POST http://127.0.0.1:%d%s",
        port,
        SLACK_EVENTS_PATH,
    )
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()
