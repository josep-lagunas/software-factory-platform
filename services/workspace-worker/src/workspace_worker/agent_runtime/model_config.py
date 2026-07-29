"""Per-role agent model routing (SFP-37 / Jira SFP-54).

A typed, validation-enforcing role→model map. This is the per-role routing
surface the workspace worker consults to choose which model id each agent role
runs against. It complements :class:`WorkspaceWorkerSettings`, which still owns
the single global default model (ID-020) — that default is the *floor* every
role falls back to when no per-role override is configured.

Binding decisions (Orchestrator, 2026-07-29 — resolve the readiness-gate
ambiguities):
- The role universe with per-role overrides is exactly
  :data:`ROLES_WITH_OVERRIDE` = ``("planner", "coder", "reviewer")`` (ID-063).
  Every other role actually in use (``test_designer``, ``readiness``, future
  roles) resolves to the global default — there is no per-role env var for them.
- The fallback chain is ``SFP_AGENT_MODEL_<ROLE>`` (planner/coder/reviewer only)
  → ``WorkspaceWorkerSettings.default_model``. Per-role vars are OPTIONAL
  overrides; the global default is the floor, so a role ALWAYS resolves to a
  non-empty model id (SFP-53 startup-validates ``default_model`` non-empty).
- No new env var for a global default (reuse ``default_model``); no hardcoded
  model-id string.

This module is pure / side-effect-free: it holds no I/O and reads no env. The
caller (:class:`~workspace_worker.agent_runtime.runtime.ClaudeAgentRuntime`)
constructs an :class:`AgentModelConfig` and consults
:meth:`AgentModelConfig.resolve` per run.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, field_validator

__all__ = ["AgentModelConfig", "ROLES_WITH_OVERRIDE"]

#: The only roles that may carry a per-role model override (ID-063). Every other
#: role resolves to the global default. This tuple is the SINGLE source of truth
#: — both the :class:`AgentModelConfig` override field names and
#: :meth:`AgentModelConfig.resolve`'s branching key off it. Adding a role here
#: requires adding a matching override field below.
ROLES_WITH_OVERRIDE: tuple[str, ...] = ("planner", "coder", "reviewer")


class AgentModelConfig(BaseModel):
    """Typed, frozen role→model routing config (SFP-37 / Jira SFP-54).

    Carries optional per-role model overrides for the roles in
    :data:`ROLES_WITH_OVERRIDE` plus a REQUIRED non-empty ``default_model`` (the
    floor every role falls back to). All four fields are stripped and rejected
    when empty/whitespace-only, mirroring
    :func:`WorkspaceWorkerSettings._check_non_empty` (SFP-53 / ID-020).

    Use :meth:`resolve` to map a role string to a concrete model id.

    Attributes:
        planner: Optional override for the ``planner`` role.
        coder: Optional override for the ``coder`` role.
        reviewer: Optional override for the ``reviewer`` role.
        default_model: REQUIRED global default; the non-empty floor for every
            role. Startup-validated non-empty here (and again by SFP-53 when
            this value is sourced from ``WorkspaceWorkerSettings.default_model``).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    planner: str | None = None
    coder: str | None = None
    reviewer: str | None = None
    default_model: str

    @field_validator("planner", "coder", "reviewer", "default_model")
    @classmethod
    def _check_non_empty(cls, value: str | None) -> str | None:
        """Reject empty/whitespace-only model ids; strip surrounding whitespace.

        ``None`` (an unset override) passes through untouched. A non-``None``
        value is stripped and must be non-empty — mirroring
        :func:`WorkspaceWorkerSettings._check_non_empty` (SFP-53 / ID-020). The
        stripped value is stored so downstream consumers never see surrounding
        whitespace ride through into ``options.model``.
        """
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("must be a non-empty, non-whitespace string")
        return stripped

    def resolve(self, role: str) -> str:
        """Resolve ``role`` to a non-empty model id (case-insensitive).

        Roles in :data:`ROLES_WITH_OVERRIDE` return their per-role override when
        one is set (not ``None``); otherwise — and for every other role — the
        global :attr:`default_model` is returned. Comparison is case-insensitive
        (``"PLANNER"`` ≡ ``"planner"``).

        ALWAYS returns a non-empty ``str``: the global default is the floor, and
        it is startup-validated non-empty (SFP-53). Pure and idempotent.
        """
        normalized = role.lower()
        if normalized in ROLES_WITH_OVERRIDE:
            # Field names mirror ROLES_WITH_OVERRIDE exactly (single source of
            # truth) — keep this map in sync if the tuple ever changes.
            overrides: dict[str, str | None] = {
                "planner": self.planner,
                "coder": self.coder,
                "reviewer": self.reviewer,
            }
            override = overrides[normalized]
            if override is not None:
                return override
        return self.default_model
