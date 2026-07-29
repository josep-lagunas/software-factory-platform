"""Tests for :class:`AgentModelConfig` per-role model routing (SFP-37 / Jira SFP-54).

Covers the binding decisions encoded in the SFP-37 doc:
- Per-role overrides for ``planner``/``coder``/``reviewer`` resolve to their own
  distinct id; unset override-in-universe roles and every other role resolve to
  the global ``default_model`` (the floor).
- ``resolve`` is case-insensitive (``"PLANNER"`` ≡ ``"planner"``).
- All four fields are stripped; whitespace-only/empty values → ``ValidationError``
  (mirrors :func:`WorkspaceWorkerSettings._check_non_empty`).
- The model is frozen and ``extra="forbid"``; assignment raises ``ValidationError``.
- ``ROLES_WITH_OVERRIDE`` is the single source of truth for the override universe.

Construction is via kwargs ONLY — no env, no BaseSettings, no monkeypatch — so
these tests exercise the pure pydantic model, not config loading.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from workspace_worker.agent_runtime.model_config import (
    ROLES_WITH_OVERRIDE,
    AgentModelConfig,
)

# --------------------------------------------------------------------------- #
# ROLES_WITH_OVERRIDE — single source of truth
# --------------------------------------------------------------------------- #


def test_roles_with_override_is_the_documented_universe() -> None:
    assert ROLES_WITH_OVERRIDE == ("planner", "coder", "reviewer")


def test_roles_with_override_is_a_tuple_of_strings() -> None:
    assert isinstance(ROLES_WITH_OVERRIDE, tuple)
    assert all(isinstance(role, str) for role in ROLES_WITH_OVERRIDE)


# --------------------------------------------------------------------------- #
# Per-role overrides resolve to their own distinct id
# --------------------------------------------------------------------------- #


def test_individual_override_resolves_to_its_own_id() -> None:
    # Only one override set at a time; each resolves to its distinct id.
    assert (
        AgentModelConfig(planner="planner-x", default_model="global").resolve("planner")
        == "planner-x"
    )
    assert AgentModelConfig(coder="coder-x", default_model="global").resolve("coder") == "coder-x"
    assert (
        AgentModelConfig(reviewer="reviewer-x", default_model="global").resolve("reviewer")
        == "reviewer-x"
    )


def test_all_three_overrides_set_each_resolve_to_their_own_id() -> None:
    cfg = AgentModelConfig(
        planner="planner-x",
        coder="coder-x",
        reviewer="reviewer-x",
        default_model="global",
    )
    assert cfg.resolve("planner") == "planner-x"
    assert cfg.resolve("coder") == "coder-x"
    assert cfg.resolve("reviewer") == "reviewer-x"
    assert cfg.default_model == "global"


def test_overrides_are_distinct_from_each_other_and_default() -> None:
    cfg = AgentModelConfig(
        planner="p-id",
        coder="c-id",
        reviewer="r-id",
        default_model="global",
    )
    resolved = {cfg.resolve(r) for r in ROLES_WITH_OVERRIDE}
    # Three distinct overrides, none equal to the global default.
    assert resolved == {"p-id", "c-id", "r-id"}
    assert "global" not in resolved


# --------------------------------------------------------------------------- #
# Unset override in universe → default
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("role", list(ROLES_WITH_OVERRIDE))
def test_unset_override_in_universe_falls_back_to_default(role: str) -> None:
    cfg = AgentModelConfig(default_model="global")  # no overrides set
    assert cfg.resolve(role) == "global"


def test_mixed_set_and_unset_overrides() -> None:
    cfg = AgentModelConfig(planner="planner-x", default_model="global")
    assert cfg.resolve("planner") == "planner-x"
    # coder/reviewer are in the universe but unset → default.
    assert cfg.resolve("coder") == "global"
    assert cfg.resolve("reviewer") == "global"


# --------------------------------------------------------------------------- #
# Unknown / out-of-universe roles → default
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "role",
    [
        "test_designer",  # real role in use (readiness-gate universe), not overridable
        "readiness",  # real role, _AGENT in readiness_gate.py:67
        "orchestrator",  # arbitrary future role
        "",  # empty role string
    ],
)
def test_unknown_role_resolves_to_default(role: str) -> None:
    cfg = AgentModelConfig(
        planner="planner-x",
        coder="coder-x",
        reviewer="reviewer-x",
        default_model="global",
    )
    assert cfg.resolve(role) == "global"


# --------------------------------------------------------------------------- #
# resolve() ALWAYS returns a non-empty str (the floor)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "role,expected",
    [
        ("planner", "planner-x"),
        ("coder", "coder-x"),
        ("reviewer", "reviewer-x"),
        ("test_designer", "global"),
        ("readiness", "global"),
        ("orchestrator", "global"),
        ("", "global"),
    ],
)
def test_resolve_always_returns_a_non_empty_str(role: str, expected: str) -> None:
    cfg = AgentModelConfig(
        planner="planner-x",
        coder="coder-x",
        reviewer="reviewer-x",
        default_model="global",
    )
    result = cfg.resolve(role)
    assert isinstance(result, str)
    assert result == expected
    assert result.strip() != ""  # non-empty floor invariant


# --------------------------------------------------------------------------- #
# Case-insensitivity
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("role", ["PLANNER", "Planner", "pLaNnEr"])
def test_resolve_is_case_insensitive(role: str) -> None:
    cfg = AgentModelConfig(planner="planner-x", default_model="global")
    assert cfg.resolve(role) == "planner-x"


@pytest.mark.parametrize("role", ["CODER", "Coder", "REVIEWER", "Reviewer"])
def test_case_insensitive_for_all_override_roles(role: str) -> None:
    cfg = AgentModelConfig(
        coder="coder-x",
        reviewer="reviewer-x",
        default_model="global",
    )
    expected = f"{role.lower()}-x"
    assert cfg.resolve(role) == expected


def test_case_insensitive_unknown_role_still_default() -> None:
    # An uppercase unknown role still falls through to the default.
    cfg = AgentModelConfig(default_model="global")
    assert cfg.resolve("TEST_DESIGNER") == "global"
    assert cfg.resolve("READINESS") == "global"


# --------------------------------------------------------------------------- #
# Determinism / idempotency
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("role", ["planner", "coder", "reviewer", "readiness"])
def test_resolve_is_deterministic_across_calls(role: str) -> None:
    cfg = AgentModelConfig(
        planner="planner-x",
        coder="coder-x",
        reviewer="reviewer-x",
        default_model="global",
    )
    first = cfg.resolve(role)
    for _ in range(5):
        assert cfg.resolve(role) == first


def test_resolve_is_pure_no_mutation() -> None:
    cfg = AgentModelConfig(planner="planner-x", default_model="global")
    for role in ["planner", "PLANNER", "coder", "readiness", "", "orchestrator"]:
        cfg.resolve(role)
    # Resolving never mutates the config.
    assert cfg.planner == "planner-x"
    assert cfg.coder is None
    assert cfg.reviewer is None
    assert cfg.default_model == "global"


# --------------------------------------------------------------------------- #
# Whitespace stripping on construction (all four fields)
# --------------------------------------------------------------------------- #


def test_overrides_are_stripped_on_construction() -> None:
    cfg = AgentModelConfig(
        planner="  planner-x  ",
        coder="\tcoder-x\n",
        reviewer=" reviewer-x ",
        default_model="  global  ",
    )
    assert cfg.planner == "planner-x"
    assert cfg.coder == "coder-x"
    assert cfg.reviewer == "reviewer-x"
    assert cfg.default_model == "global"
    # Stripped values ride through resolve().
    assert cfg.resolve("planner") == "planner-x"
    # An unknown role still resolves to the stripped default.
    assert cfg.resolve("orchestrator") == "global"


@pytest.mark.parametrize("blank", ["  ", "\t\n", ""])
def test_blank_per_role_override_rejected(blank: str) -> None:
    with pytest.raises(ValidationError):
        AgentModelConfig(planner=blank, default_model="global")


@pytest.mark.parametrize("blank", ["  ", "\t\n", ""])
def test_blank_default_model_rejected(blank: str) -> None:
    with pytest.raises(ValidationError):
        AgentModelConfig(default_model=blank)


@pytest.mark.parametrize("blank", ["  ", "\t\n", ""])
def test_blank_value_rejected_for_every_override_field(blank: str) -> None:
    for field in ROLES_WITH_OVERRIDE:
        with pytest.raises(ValidationError):
            AgentModelConfig(**{field: blank}, default_model="global")  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Missing required default_model → ValidationError
# --------------------------------------------------------------------------- #


def test_missing_default_model_rejected() -> None:
    with pytest.raises(ValidationError):
        AgentModelConfig(planner="planner-x")  # type: ignore[call-arg]


def test_explicit_none_override_is_allowed() -> None:
    # None is a valid "unset" for the optional override fields.
    cfg = AgentModelConfig(planner=None, coder=None, reviewer=None, default_model="global")
    assert cfg.planner is None
    assert cfg.resolve("planner") == "global"


# --------------------------------------------------------------------------- #
# Frozen + extra="forbid"
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("field", ["planner", "coder", "reviewer", "default_model"])
def test_assignment_to_frozen_field_raises(field: str) -> None:
    cfg = AgentModelConfig(planner="planner-x", default_model="global")
    with pytest.raises(ValidationError):
        setattr(cfg, field, "mutated")


def test_extra_field_rejected() -> None:
    with pytest.raises(ValidationError):
        AgentModelConfig(default_model="global", orchestrator="sneaky")  # type: ignore[call-arg]
