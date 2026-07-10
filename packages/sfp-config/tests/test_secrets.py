"""Tests for :class:`SecretRef` (opaque, frozen, value-free reference)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from sfp_config.secrets import SecretRef


def test_default_version_is_none() -> None:
    ref = SecretRef(name="db/password")
    assert ref.name == "db/password"
    assert ref.version is None


def test_explicit_version() -> None:
    ref = SecretRef(name="db/password", version="v1")
    assert ref.version == "v1"


def test_frozen_model_rejects_assignment() -> None:
    ref = SecretRef(name="db/password")
    with pytest.raises(ValidationError):
        ref.name = "other"  # type: ignore[misc]


def test_extra_forbidden() -> None:
    with pytest.raises(ValidationError):
        SecretRef(name="db/password", value="should-not-be-allowed")  # type: ignore[call-arg]


def test_no_value_attribute() -> None:
    ref = SecretRef(name="db/password", version="v1")
    assert not hasattr(ref, "value")


def test_str_and_repr_contain_name_not_value() -> None:
    ref = SecretRef(name="db/password", version="v1")
    for text in (str(ref), repr(ref)):
        assert "db/password" in text
        # Never leak a value-like token; version is metadata, not a secret value.
        assert "value=" not in text
