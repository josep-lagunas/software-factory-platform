"""Tests for the base :class:`Settings` (env-driven loading)."""

from __future__ import annotations

from typing import Literal

import pytest
from pydantic import ValidationError
from pydantic_settings import SettingsConfigDict
from sfp_config.settings import Settings


def test_default_env_is_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SFP_ENV", raising=False)
    settings = Settings()
    assert settings.env == "local"


def test_env_loaded_from_sfp_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SFP_ENV", raising=False)
    monkeypatch.setenv("SFP_ENV", "dev")
    settings = Settings()
    assert settings.env == "dev"


def test_invalid_env_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SFP_ENV", raising=False)
    monkeypatch.setenv("SFP_ENV", "qa")
    with pytest.raises(ValidationError):
        Settings()


def test_subclass_can_add_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    class SubSettings(Settings):
        model_config = SettingsConfigDict(
            env_prefix="SFP_",
            env_file=".env",
            env_file_encoding="utf-8",
            case_sensitive=False,
            extra="ignore",
        )
        deployment: Literal["a", "b"] = "a"

    monkeypatch.delenv("SFP_DEPLOYMENT", raising=False)
    monkeypatch.setenv("SFP_DEPLOYMENT", "b")
    sub = SubSettings()
    assert sub.deployment == "b"
    # Inherits the base field too.
    assert sub.env in ("local", "dev", "prod")
