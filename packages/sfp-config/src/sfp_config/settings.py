"""Typed runtime settings loaded from the environment.

The composition root constructs a :class:`Settings` instance (or a service
subclass) from environment variables / a local ``.env`` file and injects it
into the services that need it. Configuration is injected, never hardcoded
(ID-052, MAS §10.7). Secret-bearing config holds
:class:`~sfp_config.secrets.SecretRef` instances only — never raw secret values
(ID-016, MAS §10.8).

This is the *base* settings surface; each service/package is expected to
subclass it, extending the prefix or adding fields as needed. Per-role agent
model routing (``AgentModelConfig``) lives in SFP-41 and is intentionally not
present here.
"""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Base runtime settings for the SFP platform.

    Subclass to add service/package-specific fields; you may override
    ``model_config`` (e.g. a different ``env_prefix``) in subclasses.
    """

    # Annotated ClassVar to match BaseSettings.model_config
    # (ClassVar[SettingsConfigDict]) and satisfy strict-mypy override checks.
    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="SFP_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    env: Literal["local", "dev", "prod"] = "local"
    """Deployment environment. Drives profile selection at the composition root."""
