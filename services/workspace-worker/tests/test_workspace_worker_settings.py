"""Settings-parsing tests for the SFP-251 launch knobs.

``WorkspaceWorkerSettings`` carries the per-role turn bounds + the Coder
effort tier as committed, MEASURED defaults (planner=15, test_designer=30,
coder=80, reviewer=30, readiness=8, effort='medium'), each env-tunable via its
``SFP_``-prefixed name. These rows pin the three properties the ticket
requires: the defaults, the env-override surface, and loud failure (pydantic
``ValidationError``) on invalid values.

All rows construct with ``_env_file=None`` and clear the knob env vars first,
so a developer's stray ``.env`` / exported shell var can never skew a pin —
CI and a local launch resolve identical values from the same empty surface.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from sfp_config import SecretRef
from workspace_worker.infrastructure.settings import WorkspaceWorkerSettings

#: field name -> (env var, committed measured default) for the turn bounds.
_TURN_FIELDS: dict[str, tuple[str, int]] = {
    "planner_max_turns": ("SFP_PLANNER_MAX_TURNS", 15),
    "test_designer_max_turns": ("SFP_TEST_DESIGNER_MAX_TURNS", 30),
    "coder_max_turns": ("SFP_CODER_MAX_TURNS", 80),
    "reviewer_max_turns": ("SFP_REVIEWER_MAX_TURNS", 30),
    "readiness_max_turns": ("SFP_READINESS_MAX_TURNS", 8),
}

#: every SFP-251 knob env name (turn bounds + effort).
_KNOB_ENV_VARS = (*[env for env, _ in _TURN_FIELDS.values()], "SFP_CODER_EFFORT")


def _settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> WorkspaceWorkerSettings:
    """Build settings hermetically: no env file, all knobs cleared, then apply
    the explicit ``env`` overrides under test (required fields via kwargs)."""
    for knob in _KNOB_ENV_VARS:
        monkeypatch.delenv(knob, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return WorkspaceWorkerSettings(
        anthropic_base_url="https://llm.example.com",
        default_model="glm-x",
        llm_provider_secret_ref=SecretRef(name="LLM_TOKEN"),
        _env_file=None,
    )


def test_defaults_are_the_measured_operational_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty env -> the committed measured defaults (CI == local)."""
    settings = _settings(monkeypatch)
    assert settings.planner_max_turns == 15
    assert settings.test_designer_max_turns == 30
    assert settings.coder_max_turns == 80
    assert settings.reviewer_max_turns == 30
    assert settings.readiness_max_turns == 8
    assert settings.coder_effort == "medium"


@pytest.mark.parametrize(("field", "env_and_default"), sorted(_TURN_FIELDS.items()))
def test_each_turn_bound_is_env_tunable(
    monkeypatch: pytest.MonkeyPatch, field: str, env_and_default: tuple[str, int]
) -> None:
    """Each ``SFP_<ROLE>_MAX_TURNS`` env var overrides only its own field."""
    env_var, default = env_and_default
    settings = _settings(monkeypatch, **{env_var: str(default * 2)})
    assert getattr(settings, field) == default * 2
    # Every OTHER knob keeps its committed default (no cross-contamination).
    for other_field, (_other_env, other_default) in _TURN_FIELDS.items():
        if other_field != field:
            assert getattr(settings, other_field) == other_default
    assert settings.coder_effort == "medium"


@pytest.mark.parametrize("tier", ["low", "medium", "high"])
def test_coder_effort_accepts_the_allowed_tiers(monkeypatch: pytest.MonkeyPatch, tier: str) -> None:
    """All three effort tiers parse; none is privileged."""
    assert _settings(monkeypatch, SFP_CODER_EFFORT=tier).coder_effort == tier


@pytest.mark.parametrize(("field", "env_and_default"), sorted(_TURN_FIELDS.items()))
@pytest.mark.parametrize("bad", ["0", "-1"])
def test_zero_or_negative_turn_bound_is_rejected(
    monkeypatch: pytest.MonkeyPatch, field: str, env_and_default: tuple[str, int], bad: str
) -> None:
    """A zero/negative bound fails loud (pydantic) — a disabled bound would
    hang the pipeline instead of failing fast."""
    env_var, _ = env_and_default
    with pytest.raises(ValidationError, match=field):
        _settings(monkeypatch, **{env_var: bad})


@pytest.mark.parametrize(("field", "env_and_default"), sorted(_TURN_FIELDS.items()))
def test_non_numeric_turn_bound_is_rejected(
    monkeypatch: pytest.MonkeyPatch, field: str, env_and_default: tuple[str, int]
) -> None:
    """A non-numeric bound fails loud at construction, not at first use."""
    env_var, _ = env_and_default
    with pytest.raises(ValidationError, match=field):
        _settings(monkeypatch, **{env_var: "forty"})


@pytest.mark.parametrize("bogus", ["MEDIUM", "ultra", "", "medium ", "null"])
def test_bogus_coder_effort_is_rejected(monkeypatch: pytest.MonkeyPatch, bogus: str) -> None:
    """Any string outside {low, medium, high} — including case/whitespace
    variants — fails loud (the Literal tier set, not a silent fallback)."""
    with pytest.raises(ValidationError, match="coder_effort"):
        _settings(monkeypatch, SFP_CODER_EFFORT=bogus)


def test_kwargs_override_defaults_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """The field-name (kwargs) path also overrides — populate_by_name keeps
    programmatic construction (tests, future callers) aligned with env."""
    settings = WorkspaceWorkerSettings(
        anthropic_base_url="https://llm.example.com",
        default_model="glm-x",
        llm_provider_secret_ref=SecretRef(name="LLM_TOKEN"),
        _env_file=None,
        **{"planner_max_turns": 25, "coder_effort": "high"},  # type: ignore[arg-type]
    )
    assert settings.planner_max_turns == 25
    assert settings.coder_effort == "high"
