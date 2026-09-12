"""Tests for the external-events dev webhook runner (SFP-132).

The runner replaces the deleted communication dev runner (PR #163 drift,
ID-076): ALL ingress lives in external-events (MAS §9.2), so the local
tunnel-recipe dev serving lives here too. Covers the wiring
(:func:`~external_events.entrypoints.dev_webhook.build_dev_app` composes the
real SFP-120 stack — resolver, publisher, bus — over a seeded in-memory dev
database), the CLI/env/default resolution for port and endpoint id, and
``main()`` handing the wired app to uvicorn — with uvicorn stubbed so no
socket is ever bound. Deterministic throughout (MAS §12.7): no network, no
wall-clock dependence in the assertions.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest
from external_events.application.endpoint_resolver import (
    EndpointConfigNotFoundError,
    EndpointConfigResolver,
)
from external_events.entrypoints import dev_webhook
from external_events.entrypoints.dev_webhook import (
    DEFAULT_ENDPOINT_ID,
    DEFAULT_PORT,
    ENDPOINT_ID_ENV_VAR,
    PORT_ENV_VAR,
    build_dev_app,
)
from external_events.infrastructure.persistence import EndpointStatus
from external_events.interfaces import ChallengeEchoMiddleware, WebhookIngressEndpoint
from sfp_messaging.transport.in_memory import InMemoryTransport

# --------------------------------------------------------------------------- #
# Wiring
# --------------------------------------------------------------------------- #


class TestBuildDevApp:
    def test_wires_the_real_stack_over_a_seeded_dev_database(self) -> None:
        app, bus, resolver = build_dev_app()

        # SFP-254: the served app is the challenge-echo middleware AROUND
        # the real SFP-120 endpoint (tunnel dogfooding passes Slack's
        # save-time url_verification).
        assert isinstance(app, ChallengeEchoMiddleware)
        assert isinstance(app.app, WebhookIngressEndpoint)
        assert isinstance(bus, InMemoryTransport)
        assert isinstance(resolver, EndpointConfigResolver)
        assert bus.published_messages == []

        resolved = resolver.resolve(DEFAULT_ENDPOINT_ID)
        assert resolved.provider == "slack"
        assert resolved.auth_strategy == "slack_signature"
        assert resolved.secret_ref == "SLACK_SIGNING_SECRET"
        assert resolved.status is EndpointStatus.ACTIVE

    def test_seeds_the_requested_endpoint_id_only(self) -> None:
        """``--endpoint-id`` changes the seeded row — the dev row is the route."""
        _app, _bus, resolver = build_dev_app("slack-ops")

        assert resolver.resolve("slack-ops").provider == "slack"
        with pytest.raises(EndpointConfigNotFoundError):
            resolver.resolve(DEFAULT_ENDPOINT_ID)


# --------------------------------------------------------------------------- #
# CLI / env / default resolution
# --------------------------------------------------------------------------- #


class TestResolution:
    def test_cli_port_wins_over_env_and_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(PORT_ENV_VAR, "9000")
        assert dev_webhook._resolve_port(9911) == 9911

    def test_env_port_wins_over_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(PORT_ENV_VAR, "9000")
        assert dev_webhook._resolve_port(None) == 9000

    def test_default_port_when_no_cli_and_no_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(PORT_ENV_VAR, raising=False)
        assert dev_webhook._resolve_port(None) == DEFAULT_PORT

    def test_non_numeric_env_port_falls_back_to_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(PORT_ENV_VAR, "not-a-port")
        assert dev_webhook._resolve_port(None) == DEFAULT_PORT

    def test_cli_endpoint_id_wins_over_env_and_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(ENDPOINT_ID_ENV_VAR, "from-env")
        assert dev_webhook._resolve_endpoint_id("from-cli") == "from-cli"

    def test_env_endpoint_id_wins_over_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ENDPOINT_ID_ENV_VAR, "from-env")
        assert dev_webhook._resolve_endpoint_id(None) == "from-env"

    def test_blank_env_endpoint_id_falls_back_to_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(ENDPOINT_ID_ENV_VAR, "   ")
        assert dev_webhook._resolve_endpoint_id(None) == DEFAULT_ENDPOINT_ID

    def test_default_endpoint_id_when_no_cli_and_no_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(ENDPOINT_ID_ENV_VAR, raising=False)
        assert dev_webhook._resolve_endpoint_id(None) == DEFAULT_ENDPOINT_ID


# --------------------------------------------------------------------------- #
# main() — uvicorn stubbed, no socket bound
# --------------------------------------------------------------------------- #


def _stub_uvicorn(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Install a fake ``uvicorn`` module recording ``run`` calls."""
    calls: list[dict[str, Any]] = []
    fake_uvicorn = types.ModuleType("uvicorn")
    fake_uvicorn.run = lambda app, **kw: calls.append({"app": app, **kw})  # type: ignore[assignment]
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)
    return calls


class TestMain:
    def test_serves_the_wired_app_on_the_resolved_port(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(PORT_ENV_VAR, raising=False)
        monkeypatch.delenv(ENDPOINT_ID_ENV_VAR, raising=False)
        monkeypatch.setattr(dev_webhook.logger, "info", lambda *a, **k: None)
        calls = _stub_uvicorn(monkeypatch)

        dev_webhook.main(["--port", "9911", "--endpoint-id", "slack-ops"])

        assert len(calls) == 1
        assert isinstance(calls[0]["app"], ChallengeEchoMiddleware)
        assert isinstance(calls[0]["app"].app, WebhookIngressEndpoint)
        assert calls[0]["port"] == 9911
        assert calls[0]["host"] == "127.0.0.1"

    def test_defaults_to_default_port_and_endpoint_id(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(PORT_ENV_VAR, raising=False)
        monkeypatch.delenv(ENDPOINT_ID_ENV_VAR, raising=False)
        monkeypatch.setattr(dev_webhook.logger, "info", lambda *a, **k: None)
        calls = _stub_uvicorn(monkeypatch)

        dev_webhook.main([])

        assert len(calls) == 1
        assert calls[0]["port"] == DEFAULT_PORT
