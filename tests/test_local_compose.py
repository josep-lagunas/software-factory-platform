"""Local-dev Docker Compose smoke test (SFP-77 / doc SFP-60).

Asserts ``infrastructure/local/compose.yaml`` parses and declares the LocalStack
service (SNS/SQS/DLQ emulation) with its edge port and service list.

PyYAML is used only when already installed; the whole module is skipped
otherwise. This ticket ships a YAML stanza (no Python helper code), so the
helper-coverage AC is vacuous and PyYAML is NOT added as a project dependency.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("yaml")  # skip the whole module if PyYAML is absent
import yaml  # noqa: E402  (must follow the importorskip gate above)

COMPOSE = Path(__file__).resolve().parents[1] / "infrastructure" / "local" / "compose.yaml"
_DATA = yaml.safe_load(COMPOSE.read_text())


def test_compose_has_services_toplevel():
    assert isinstance(_DATA, dict)
    assert "services" in _DATA


def test_localstack_service_is_declared():
    assert "localstack" in _DATA["services"]


def test_localstack_maps_edge_port_4566():
    ports = _DATA["services"]["localstack"].get("ports", [])
    assert any("4566" in str(port) for port in ports), ports


def test_localstack_emulates_sns_and_sqs():
    env = _DATA["services"]["localstack"].get("environment", {})
    services_env = env.get("SERVICES", "")
    assert "sns" in services_env, services_env
    assert "sqs" in services_env, services_env


def test_localstack_uses_unless_stopped_and_named_volume():
    assert _DATA["services"]["localstack"].get("restart") == "unless-stopped"
    assert "sfp-localstack-data" in _DATA.get("volumes", {})
