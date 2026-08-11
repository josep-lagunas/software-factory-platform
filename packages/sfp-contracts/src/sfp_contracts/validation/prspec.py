"""Structural validation of a Planner-emitted PRSpec (ID-021 / SFP-14).

Promotes the collect-all, no-short-circuit structural linter that previously
lived as a stdlib-only script in ``tools/check_prspec.py`` (SFP-193) into the
typed, CI-mypy-checked ``sfp_contracts`` package. The linter validates that a
PRSpec carries every required top-level key, that each ``modify`` file entry is
*execution-pinned* (exactly one anchor of ``before`` text or ``line_range``),
and that any declared ``deferred_fk_obligations`` carry the ID-058 deferral
shape. This front-loads determinism: a spec that fails here never reaches the
Coder.

Non-goal: this module deliberately does NOT replace :func:`validate` with
``PrSpec.model_validate(...)``. Pydantic's ``model_validate`` is *fail-fast* —
it raises :class:`~pydantic.ValidationError` on the first structural problem.
The structural linter's value is the opposite: it **collects every violation in
a single pass** so a Planner run sees the whole gap list at once rather than
fixing-and-rerunning N times. The collect-all semantics are preserved here
verbatim; the pydantic-swap is a future consideration, explicitly deferred.

Grounded in:
- ID-021 — the intent this promotion fulfils (``tools/check_prspec.py`` carried
  it as a TODO for as long as the contracts package had not landed).
- SFP-14 — the implementation ticket for the typed ``PrSpec`` payload.
- ID-058 — the intra-service FK deferral protocol whose shape is validated here
  via :class:`~sfp_contracts.agents.planner.DeferredForeignKeyObligation` (the
  contract authority for the required keys).
- SFP-234 — the amendment that made the deferral shape explicit in the linter.
"""

import functools
from collections.abc import Mapping
from typing import Any

# ============================================================
# CONTRACT — required top-level keys + controlled vocabularies
# ============================================================

#: The top-level keys every PRSpec MUST carry (presence + shape only; extra
#: unknown keys are NOT rejected by :func:`validate_prspec`).
REQUIRED_TOP_LEVEL_KEYS: tuple[str, ...] = (
    "pr_spec_id",
    "ticket",
    "title",
    "branch_name",
    "validation_profile_acknowledged",
    "files",
    "implementation_steps",
    "dependencies",
    "risks",
    "commit_plan",
    "pr_title",
    "pr_body_must_include",
    "acceptance_criteria_mapping",
    "verification",
    "read_allowlist",
    "rig_reference",
)

#: Controlled vocabulary for a file entry's ``action`` field.
VALID_ACTIONS: tuple[str, ...] = ("create", "modify", "delete")

#: Controlled vocabulary for the ``verification.type`` field.
VALID_VERIFICATION_TYPES: tuple[str, ...] = ("script", "command")


@functools.cache
def _deferred_fk_required_keys() -> tuple[str, ...]:
    """The required keys for a ``deferred_fk_obligations`` entry, drawn directly
    from :class:`~sfp_contracts.agents.planner.DeferredForeignKeyObligation`.

    The model is the single source of truth (ID-058): if it gains a field, the
    linter enforces it automatically without an edit here.

    The import is deferred to runtime to avoid an import-cycle: the
    ``agents.planner`` module depends on ``validation.profiles`` (its
    :class:`~sfp_contracts.validation.profiles.ValidationProfile` field), so
    importing it at this module's top level would make ``validation`` depend on
    ``agents`` and close a cycle. Importing inside the function resolves only
    after both packages are fully initialised.
    """
    from sfp_contracts.agents.planner import DeferredForeignKeyObligation

    return tuple(DeferredForeignKeyObligation.model_fields.keys())


# ============================================================
# VALIDATION — collects ALL violations (no short-circuit)
# ============================================================


def validate_prspec(spec: Mapping[str, Any]) -> list[str]:
    """Validate a PRSpec mapping.

    Returns a list of human-readable violation strings (empty == valid).
    Collects every violation in a single pass; never short-circuits.

    The collect-all behaviour is intentional and must be preserved: pydantic's
    ``PrSpec.model_validate`` is fail-fast with different semantics and is NOT a
    drop-in replacement (see the module docstring — that swap is deferred).
    """
    violations: list[str] = []

    if not isinstance(spec, dict):
        return [f"spec must be a JSON object (dict); got {type(spec).__name__}"]

    # ---- required top-level keys ----------------------------------------
    for key in REQUIRED_TOP_LEVEL_KEYS:
        if key not in spec:
            violations.append(f"missing required top-level key: '{key}'")

    # ---- files ----------------------------------------------------------
    if "files" in spec:
        violations.extend(_check_files(spec["files"]))

    # ---- verification ---------------------------------------------------
    if "verification" in spec:
        v = spec["verification"]
        if not isinstance(v, dict):
            violations.append(f"'verification' must be a dict (got {type(v).__name__})")
        else:
            vtype = v.get("type")
            if vtype not in VALID_VERIFICATION_TYPES:
                violations.append(
                    f"'verification.type' must be one of {VALID_VERIFICATION_TYPES} (got {vtype!r})"
                )
            body = v.get("body")
            if not isinstance(body, str) or not body.strip():
                violations.append("'verification.body' must be a non-empty string")

    # ---- read_allowlist -------------------------------------------------
    if "read_allowlist" in spec:
        ra = spec["read_allowlist"]
        if not isinstance(ra, list) or len(ra) == 0:
            violations.append("'read_allowlist' must be a non-empty list")

    # ---- rig_reference --------------------------------------------------
    if "rig_reference" in spec:
        rr = spec["rig_reference"]
        if not isinstance(rr, str) or not rr.strip():
            violations.append("'rig_reference' must be a non-empty string")

    # ---- commit_plan ----------------------------------------------------
    if "commit_plan" in spec:
        cp = spec["commit_plan"]
        if not isinstance(cp, dict):
            violations.append("'commit_plan' must be a dict")
        else:
            strategy = cp.get("strategy")
            if not isinstance(strategy, str) or not strategy.strip():
                violations.append("'commit_plan.strategy' must be a non-empty string")
            cm = cp.get("commit_message")
            if not isinstance(cm, str) or not cm.strip():
                violations.append("'commit_plan.commit_message' must be a non-empty string")

    # ---- risks ----------------------------------------------------------
    if "risks" in spec:
        r = spec["risks"]
        if not isinstance(r, list) or len(r) == 0:
            violations.append("'risks' must be a non-empty list")

    # ---- implementation_steps ------------------------------------------
    if "implementation_steps" in spec:
        steps = spec["implementation_steps"]
        if not isinstance(steps, list) or len(steps) == 0:
            violations.append("'implementation_steps' must be a non-empty list")

    # ---- dependencies (dict OR list both OK) ---------------------------
    if "dependencies" in spec:
        d = spec["dependencies"]
        if not isinstance(d, (dict, list)):
            violations.append(f"'dependencies' must be a dict or list (got {type(d).__name__})")

    # ---- acceptance_criteria_mapping (must be dict) --------------------
    if "acceptance_criteria_mapping" in spec:
        acm = spec["acceptance_criteria_mapping"]
        if not isinstance(acm, dict):
            violations.append(
                "'acceptance_criteria_mapping' must be a dict "
                f"(got {type(acm).__name__}; list/scalar rejected)"
            )

    # ---- deferred_fk_obligations (ID-058 deferral protocol) -------------
    # Optional (absent == no deferrals declared). When present, each entry's
    # required keys must be present AND non-empty. Collects violations; no
    # short-circuit (mirrors the existing rule style). Shape only — this linter
    # does NOT inspect Alembic migrations (that is SFP-235's concern).
    if "deferred_fk_obligations" in spec:
        violations.extend(_check_deferred_fk_obligations(spec["deferred_fk_obligations"]))

    # NOTE: unknown/extra top-level keys are NOT rejected (presence+shape
    # only). Duplicate file paths are NOT rejected either. See PRSpec SFP-193.
    return violations


#: Canonical alias — :func:`validate_prspec` is the exported name; the bare
#: ``validate`` spelling is kept for callers that pre-date the rename.
validate = validate_prspec


def _check_files(files: Any) -> list[str]:
    out: list[str] = []
    if not isinstance(files, list):
        out.append(f"'files' must be a list (got {type(files).__name__})")
        return out
    if len(files) == 0:
        out.append("'files' must be a non-empty list")
        return out
    for i, entry in enumerate(files):
        if not isinstance(entry, dict):
            out.append(f"files[{i}] must be a dict/object (got {type(entry).__name__})")
            continue
        path = entry.get("path")
        if not isinstance(path, str) or not path.strip():
            out.append(f"files[{i}] missing or empty 'path' (non-empty string required)")
        action = entry.get("action")
        if action not in VALID_ACTIONS:
            out.append(f"files[{i}] invalid 'action' {action!r} (must be one of {VALID_ACTIONS})")
        # create/delete with an anchor present is OK (ignored, not rejected).
        if action == "modify":
            out.extend(_check_anchor(entry.get("anchor"), i))
    return out


def _check_anchor(anchor: Any, i: int) -> list[str]:
    """Validate a modify entry's anchor: EXACTLY ONE of before/line_range."""
    out: list[str] = []
    # Missing anchor key (entry.get returns None) OR explicit None.
    if anchor is None:
        out.append(f"files[{i}] action=modify REQUIRES an 'anchor' (missing)")
        return out
    if not isinstance(anchor, dict):
        out.append(f"files[{i}] 'anchor' must be a dict (got {type(anchor).__name__})")
        return out
    has_before = "before" in anchor
    has_range = "line_range" in anchor
    if has_before and has_range:
        out.append(f"files[{i}] 'anchor' must have EXACTLY ONE of before/line_range (both present)")
        return out
    if not has_before and not has_range:
        out.append(
            f"files[{i}] 'anchor' must have EXACTLY ONE of before/line_range (neither present)"
        )
        return out
    if has_before:
        b = anchor["before"]
        if not isinstance(b, str) or not b.strip():
            out.append(f"files[{i}] anchor.before must be a non-empty string")
        return out
    # line_range path
    lr = anchor["line_range"]
    if not isinstance(lr, list) or len(lr) != 2:
        out.append(f"files[{i}] anchor.line_range must be a 2-element list [start, end]")
        return out
    start, end = lr[0], lr[1]
    # Reject bools explicitly: isinstance(True, int) is True in Python, so the
    # int check alone would let True/False through.
    if (
        isinstance(start, bool)
        or isinstance(end, bool)
        or not isinstance(start, int)
        or not isinstance(end, int)
    ):
        out.append(f"files[{i}] anchor.line_range elements must be ints (bools rejected)")
        return out
    if start < 1:
        out.append(f"files[{i}] anchor.line_range start must be >= 1 (got {start})")
    if end < start:
        out.append(f"files[{i}] anchor.line_range end ({end}) must be >= start ({start})")
    return out


def _check_deferred_fk_obligations(entries: Any) -> list[str]:
    """Validate the shape of ``deferred_fk_obligations`` (ID-058 deferral protocol).

    Each entry must be a dict carrying every key returned by
    :func:`_deferred_fk_required_keys` (the field set of
    :class:`~sfp_contracts.agents.planner.DeferredForeignKeyObligation`) as a
    non-empty string. Violations are collected per entry per key (no
    short-circuit), matching the style of :func:`_check_files` /
    :func:`_check_anchor`. This is shape validation only; it does NOT inspect
    Alembic migrations (that is SFP-235's concern).
    """
    out: list[str] = []
    if not isinstance(entries, list):
        out.append(f"'deferred_fk_obligations' must be a list (got {type(entries).__name__})")
        return out
    required_keys = _deferred_fk_required_keys()
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            out.append(
                f"deferred_fk_obligations[{i}] must be a dict/object (got {type(entry).__name__})"
            )
            continue
        for key in required_keys:
            val = entry.get(key)
            if not isinstance(val, str) or not val.strip():
                out.append(
                    f"deferred_fk_obligations[{i}] missing or empty '{key}' "
                    f"(non-empty string required)"
                )
    return out


__all__ = [
    "REQUIRED_TOP_LEVEL_KEYS",
    "VALID_ACTIONS",
    "VALID_VERIFICATION_TYPES",
    "validate",
    "validate_prspec",
]
