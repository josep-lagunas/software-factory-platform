"""Tests for the ``EndpointConfig`` persistence model (ID-058, MAS §9.2).

These tests exercise the ORM model declaration directly against the table
metadata, without a live database: they assert the column shapes, constraints,
enum wiring, and construction behavior that the Alembic baseline migration
must mirror. No DB session is required, keeping the suite fast and hermetic.
"""

from __future__ import annotations

import pytest
from external_events.infrastructure.persistence import (
    Base,
    EndpointConfig,
    EndpointStatus,
)
from external_events.infrastructure.persistence.models import (
    OPERATIONAL_SCHEMA,
    _enum_values,
)

# Columns that must be NOT NULL per MAS §9.2 endpoint-configuration contract.
_REQUIRED_COLUMNS = (
    "endpoint_id",
    "provider",
    "auth_strategy",
    "secret_ref",
    "status",
    "created_at",
    "updated_at",
)


@pytest.fixture
def columns() -> dict[str, object]:
    """Return the model's columns keyed by name."""
    return {col.name: col for col in EndpointConfig.__table__.columns}


def _make_endpoint(**overrides: object) -> EndpointConfig:
    defaults: dict[str, object] = {
        "endpoint_id": "gh-webhook",
        "provider": "github",
        "auth_strategy": "hmac_sha256",
        "secret_ref": "op://prod/github/webhook",
        "status": EndpointStatus.ACTIVE,
    }
    defaults.update(overrides)
    return EndpointConfig(**defaults)


class TestEndpointStatus:
    def test_values_are_the_accept_reject_pair(self):
        # MAS §9.2 ingress validation only needs the accept/reject decision.
        assert {e.value for e in EndpointStatus} == {"active", "inactive"}

    def test_str_is_the_persisted_value(self):
        assert str(EndpointStatus.ACTIVE) == "active"
        assert str(EndpointStatus.INACTIVE) == "inactive"


def test_enum_values_helper_returns_member_values():
    # The model persists ``.value`` (not ``.name``); the helper backs the
    # SQLAlchemy ``values_callable``.
    assert _enum_values(EndpointStatus) == ["active", "inactive"]


class TestEndpointConfigConstruction:
    def test_construction_sets_all_fields(self):
        cfg = _make_endpoint(endpoint_metadata={"events": ["push"]})
        assert cfg.endpoint_id == "gh-webhook"
        assert cfg.provider == "github"
        assert cfg.auth_strategy == "hmac_sha256"
        assert cfg.secret_ref == "op://prod/github/webhook"
        assert cfg.status is EndpointStatus.ACTIVE
        assert cfg.endpoint_metadata == {"events": ["push"]}

    def test_metadata_is_optional(self):
        cfg = _make_endpoint()
        assert cfg.endpoint_metadata is None

    def test_repr_contains_identity_and_lifecycle(self):
        cfg = _make_endpoint()
        rendered = repr(cfg)
        assert "gh-webhook" in rendered
        assert "github" in rendered
        # status renders via the enum (name or value).
        assert "ACTIVE" in rendered or "active" in rendered


class TestEndpointConfigSchema:
    def test_table_is_in_the_operational_schema(self):
        table = EndpointConfig.__table__
        assert table.name == "endpoint_configs"
        assert table.schema == OPERATIONAL_SCHEMA == "operational"

    def test_endpoint_id_is_the_primary_key(self):
        pk_cols = [c.name for c in EndpointConfig.__table__.primary_key.columns]
        assert pk_cols == ["endpoint_id"]

    def test_required_columns_are_not_nullable(self, columns):
        for name in _REQUIRED_COLUMNS:
            assert columns[name].nullable is False, name  # type: ignore[attr-defined]

    def test_metadata_column_is_nullable(self, columns):
        assert columns["endpoint_metadata"].nullable is True  # type: ignore[attr-defined]

    def test_status_column_is_a_non_native_enum(self, columns):
        status_type = columns["status"].type  # type: ignore[attr-defined]
        assert status_type.name == "endpoint_status"
        assert status_type.native_enum is False
        assert status_type.length == 16
        assert list(status_type.enums) == ["active", "inactive"]

    def test_timestamps_are_timezone_aware_with_server_default(self, columns):
        for name in ("created_at", "updated_at"):
            col = columns[name]  # type: ignore[index]
            assert col.nullable is False
            assert col.server_default is not None
            assert col.type.timezone is True


def test_base_metadata_has_the_schema_qualified_table():
    # SQLAlchemy keys schema-qualified tables as "schema.tablename".
    assert "operational.endpoint_configs" in Base.metadata.tables
