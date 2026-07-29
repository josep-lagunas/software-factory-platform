"""Workspace-worker runtime settings (SFP-36 / Jira SFP-53).

Extends the base :class:`sfp_config.Settings` with the Anthropic-compatible
provider endpoint, the single default agent model, and the opaque reference to
the provider auth secret. Startup-validates that the endpoint and model are
configured (ID-020 — "startup validation of model config") and that the secret
reference is a typed :class:`~sfp_config.SecretRef` (never a raw credential —
ID-016 / MAS §10.8).

Per-role model routing is SFP-37's scope (it depends on this ticket); this
surface resolves exactly one default model.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import Field, field_validator
from pydantic_settings import SettingsConfigDict
from sfp_config import SecretRef, Settings


class WorkspaceWorkerSettings(Settings):
    """Workspace-worker settings: provider endpoint + model + secret ref.

    Attributes:
        anthropic_base_url: Base URL of the Anthropic-compatible provider
            endpoint the spawned agent CLI is routed to (ID-020). Required and
            must be non-empty / non-whitespace; validated at construction.
        default_model: The default model id used for every agent run. A single
            default per ID-020 (per-role routing is SFP-37's scope, not here).
            Required and non-empty / non-whitespace.
        llm_provider_secret_ref: Opaque :class:`~sfp_config.SecretRef` to the
            provider auth token, resolved at runtime via
            :class:`~sfp_config.SecretProvider`. A non-``SecretRef`` value is
            rejected at construction.
        extra_env: Optional extra ``env`` entries forwarded into the spawned
            agent process, merged AFTER the routing/auth entries (so callers
            cannot accidentally overwrite ``ANTHROPIC_*``).
    """

    # Re-declared to keep env-file / prefix behaviour explicit in this service
    # (BaseSettings inherits model_config, but the prefix is load-bearing for
    # field resolution, so we pin it here rather than rely on inheritance).
    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="SFP_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    anthropic_base_url: str
    default_model: str
    llm_provider_secret_ref: SecretRef
    extra_env: dict[str, str] = Field(default_factory=dict)

    @field_validator("anthropic_base_url", "default_model")
    @classmethod
    def _check_non_empty(cls, value: str) -> str:
        """Reject missing / whitespace-only endpoint and model (ID-020)."""
        if not value.strip():
            raise ValueError("must be a non-empty, non-whitespace string")
        return value

    @field_validator("llm_provider_secret_ref")
    @classmethod
    def _check_secret_ref(cls, value: SecretRef) -> SecretRef:
        """Reject anything that did not coerce to a typed ``SecretRef``."""
        # pydantic's own SecretRef annotation already rejects non-coercible
        # values (str/int) before this after-validator runs, so the branch below
        # is defensive only — it documents intent, not a normally-reachable path.
        if not isinstance(value, SecretRef):  # pragma: no cover
            raise ValueError("llm_provider_secret_ref must be a SecretRef")
        return value
