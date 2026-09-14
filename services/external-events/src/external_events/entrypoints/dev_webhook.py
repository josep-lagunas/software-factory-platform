"""Local dev runner for the single external-events webhook (SFP-132).

Serves the SFP-254 challenge-echo-wrapped
:class:`~external_events.interfaces.webhook.WebhookIngressEndpoint`
(SFP-120) on a configurable port (default 8789) for tunnel-based
dogfooding — point cloudflared/ngrok at it, then set the Slack app's Event
Subscriptions request URL to the tunnel's ``/webhooks/{endpoint_id}`` path.
No AWS, no cloud: the v0 hosting path is exactly this runner plus a tunnel
(production hosting is the hosting epic, deferred by design). This replaces
the deleted communication dev runner (``dev_slack_events``, PR #163 drift)
with the MAS §9.2-conformant one: ALL ingress lives here, in the External
Events Service.

Wiring (all in-process, per the software-first owner decision):
- ``LocalSecretProvider`` resolves ``SLACK_SIGNING_SECRET`` from the
  environment or ``secrets.local`` (SFP-86) — consumed by the
  ``slack_signature`` strategy (SFP-123) through the SFP-122 factory.
- :class:`~external_events.application.endpoint_resolver.EndpointConfigResolver`
  over an in-memory SQLite ``operational`` schema seeded with ONE ACTIVE
  Slack endpoint (``provider=slack``, ``auth_strategy=slack_signature``,
  ``secret_ref=SLACK_SIGNING_SECRET``). Production seeds
  ``operational.endpoint_configs`` out-of-band (ID-058, a HUMAN action);
  this dev row exists only inside this process.
- :class:`~sfp_messaging.transport.in_memory.InMemoryTransport` is the bus —
  published ``ExternalEventReceived`` events land on
  ``bus.published_messages``. The Communication consumer (SFP-132,
  ``communication.interfaces.slack_inbound``) registers on the same default
  registry when its module is imported in-process; wiring the two services
  into one process is a composition-root concern, not this runner's.

Usage::

    uv run python -m external_events.entrypoints.dev_webhook \\
        [--port 8789] [--endpoint-id slack-dev]

    # or via env (env wins only when the matching flag is absent):
    EXTERNAL_WEBHOOK_PORT=9000 \\
    EXTERNAL_WEBHOOK_ENDPOINT_ID=slack-ops \\
    uv run python -m external_events.entrypoints.dev_webhook

Challenge echo (SFP-254): the served app is
:class:`~external_events.interfaces.challenge_echo.ChallengeEchoMiddleware`
around the endpoint, so saving the Slack app's Event Subscriptions request
URL — pointing it at the tunnel's ``/webhooks/{endpoint_id}`` path
(https://api.slack.com/apps → Event Subscriptions) — now passes Slack's
save-time ``url_verification`` validation: the challenge is echoed back on
the authenticated 200. Repointing the URL remains a HUMAN ACTION (owner),
executable once this runner is up behind the tunnel.
"""

from __future__ import annotations

import argparse
import atexit
import logging
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, Final

import sqlalchemy as sa
from sfp_config import LocalSecretProvider
from sfp_messaging.transport.in_memory import InMemoryTransport
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from external_events.application.endpoint_resolver import (
    EndpointConfigResolver,
    SessionFactory,
)
from external_events.application.publisher import ExternalEventPublisher
from external_events.infrastructure.persistence import (
    Base,
    EndpointConfig,
    EndpointStatus,
)
from external_events.interfaces import ChallengeEchoMiddleware, WebhookIngressEndpoint

__all__ = [
    "DEFAULT_ENDPOINT_ID",
    "DEFAULT_PORT",
    "ENDPOINT_ID_ENV_VAR",
    "PORT_ENV_VAR",
    "build_dev_app",
]

#: Default listen port (SFP-132 dev webhook runner).
DEFAULT_PORT: Final[int] = 8789

#: Environment variable overriding the port (used when ``--port`` is absent).
PORT_ENV_VAR: Final[str] = "EXTERNAL_WEBHOOK_PORT"

#: Default dev endpoint id — the ``{endpoint_id}`` of the seeded row, i.e.
#: the tunnel URL path to configure in the Slack app.
DEFAULT_ENDPOINT_ID: Final[str] = "slack-dev"

#: Environment variable overriding the endpoint id (``--endpoint-id`` wins).
ENDPOINT_ID_ENV_VAR: Final[str] = "EXTERNAL_WEBHOOK_ENDPOINT_ID"

logger = logging.getLogger(__name__)


def _dev_session_factory(endpoint_id: str) -> SessionFactory:
    """Build the dev resolver's session factory over a seeded dev database.

    In-memory SQLite with the ``operational`` schema attached (the SFP-121
    test recipe: StaticPool shares ONE underlying connection across
    checkouts, which keeps the ATTACH — and the seeded rows — alive for the
    process lifetime; no connection is held open). One ACTIVE Slack endpoint
    row is planted under ``endpoint_id``. The engine is registered with
    :mod:`atexit` for disposal at process exit — the dev database lives
    exactly as long as the runner.
    """
    engine = sa.create_engine(
        "sqlite+pysqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @sa.event.listens_for(engine, "connect")
    def _attach_operational(dbapi_connection: object, _record: object) -> None:
        dbapi_connection.execute("ATTACH DATABASE ':memory:' AS operational")  # type: ignore[attr-defined]

    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False)

    with maker.begin() as seed_session:
        seed_session.add(
            EndpointConfig(
                endpoint_id=endpoint_id,
                provider="slack",
                auth_strategy="slack_signature",
                secret_ref="SLACK_SIGNING_SECRET",
                status=EndpointStatus.ACTIVE,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )

    @contextmanager
    def factory() -> Iterator[Session]:
        session = maker()
        try:
            yield session
        finally:
            session.close()

    # Disposed at process exit: the atexit registration also keeps a strong
    # reference, so the engine is never garbage-collected mid-session with
    # its pooled connection still open.
    atexit.register(engine.dispose)

    return factory


def build_dev_app(
    endpoint_id: str = DEFAULT_ENDPOINT_ID,
    *,
    ingress_publisher_factory: Callable[[InMemoryTransport], Any] | None = None,
) -> tuple[ChallengeEchoMiddleware, InMemoryTransport, EndpointConfigResolver]:
    """Wire the webhook with resolver + publisher + bus (dev composition).

    The endpoint is wrapped in
    :class:`~external_events.interfaces.challenge_echo.ChallengeEchoMiddleware`
    (SFP-254) so the served app answers Slack's save-time
    ``url_verification`` handshake — tunnel dogfooding passes the Slack
    app's URL validation without any provider knowledge leaking into the
    endpoint itself (ID-028).

    Args:
        endpoint_id: The dev endpoint id seeded into the resolver's database
            — the ``{endpoint_id}`` of the served ``/webhooks/{endpoint_id}``
            path. Defaults to :data:`DEFAULT_ENDPOINT_ID`.
        ingress_publisher_factory: Optional dev seam (SFP-257 round 3)
            building the ingress publisher over the created bus — the dev
            composition passes its ack-then-process publisher so the HTTP
            200 never waits on the consume chain. Defaults to ``None``:
            the plain SFP-124 :class:`ExternalEventPublisher` (publish
            inline, then 200 — the behavior the webhook contract tests
            pin).

    Returns the (wrapped) app, the bus, and the resolver so the runner (and
    tests) can inspect what was published via ``bus.published_messages`` and
    resolve the seeded endpoint configuration.
    """
    session_factory = _dev_session_factory(endpoint_id)
    resolver = EndpointConfigResolver(session_factory)
    bus = InMemoryTransport()
    publisher = (
        ingress_publisher_factory(bus)
        if ingress_publisher_factory is not None
        else ExternalEventPublisher(bus)
    )
    app = ChallengeEchoMiddleware(
        WebhookIngressEndpoint(
            resolver,
            LocalSecretProvider(),
            publisher,
            bus,
        )
    )
    return app, bus, resolver


def _resolve_port(cli_port: int | None) -> int:
    """Resolve the listen port: ``--port`` wins, then env, then default."""
    if cli_port is not None:
        return cli_port
    raw = os.environ.get(PORT_ENV_VAR, "")
    if raw.strip().isdigit():
        return int(raw)
    return DEFAULT_PORT


def _resolve_endpoint_id(cli_endpoint_id: str | None) -> str:
    """Resolve the endpoint id: ``--endpoint-id`` wins, then env, then default."""
    if cli_endpoint_id is not None:
        return cli_endpoint_id
    raw = os.environ.get(ENDPOINT_ID_ENV_VAR, "")
    if raw.strip():
        return raw.strip()
    return DEFAULT_ENDPOINT_ID


def main(argv: list[str] | None = None) -> None:
    """Parse args and serve the webhook until interrupted."""
    parser = argparse.ArgumentParser(
        prog="dev_webhook",
        description=(
            "Serve the external-events webhook locally (SFP-132) for tunnel-based dogfooding."
        ),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help=f"Listen port (default {DEFAULT_PORT}, env {PORT_ENV_VAR}).",
    )
    parser.add_argument(
        "--endpoint-id",
        type=str,
        default=None,
        help=(
            f"The seeded endpoint id served at /webhooks/{{endpoint_id}} "
            f"(default {DEFAULT_ENDPOINT_ID}, env {ENDPOINT_ID_ENV_VAR})."
        ),
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    endpoint_id = _resolve_endpoint_id(args.endpoint_id)
    app, _bus, _resolver = build_dev_app(endpoint_id)
    port = _resolve_port(args.port)

    # Imported here (not module-level) so importing this module for its
    # wiring helpers never drags the server in — tests build the app without
    # binding a socket.
    import uvicorn

    logger.info(
        "SFP-132 dev external-events webhook: POST http://127.0.0.1:%d/webhooks/%s",
        port,
        endpoint_id,
    )
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()
