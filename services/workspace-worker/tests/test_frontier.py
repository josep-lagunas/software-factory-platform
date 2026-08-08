"""Tests for :mod:`workspace_worker.workflow.frontier` (SFP-232).

Pure, deterministic unit tests — no network. Covers:

- :func:`is_manual_ticket` — the ``manual`` label membership test.
- :func:`upstream_has_manual` — transitive upstream 👤 detection over a stub
  offline DAG + offset map: manual upstream -> True; all-ai chain -> False;
  cycle safe; post-blueprint key (not in offset) -> False; missing dep key
  skipped; the ticket's own executor is NOT consulted (label clause owns that).
- :func:`compute_at_frontier` — label short-circuit; injection of stub
  ``dag``/``jira_to_doc``; fail-safe default load on missing/corrupt files.
- :func:`load_build_order_dag` / :func:`load_jira_to_doc` — file shape parsing
  against synthetic tmp files (defensive: missing fields degrade, non-str keys
  skipped).

The DAG / offset map are encoded INDEPENDENTLY here (stub fixtures), mirroring
the oracle precedent in the sibling rubric/parser tests.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest
from workspace_worker.workflow.frontier import (
    AI_AGENT_LABEL,
    MANUAL_LABEL,
    compute_at_frontier,
    is_manual_ticket,
    load_build_order_dag,
    load_jira_to_doc,
    upstream_has_manual,
)

#: The Jira key for DOC SFP-1 (offset +17) — used in stub offset maps.
_JIRA_SFP_1 = "SFP-18"
_JIRA_SFP_2 = "SFP-19"
_JIRA_SFP_3 = "SFP-20"
_JIRA_SFP_4 = "SFP-21"


# ---------------------------------------------------------------------------
# is_manual_ticket
# ---------------------------------------------------------------------------


def test_manual_label_constant() -> None:
    """Guard: the executor label constants are the documented values."""
    assert MANUAL_LABEL == "manual"
    assert AI_AGENT_LABEL == "ai-agent"


def test_is_manual_ticket_true_when_label_present() -> None:
    """The 'manual' label among others still yields True."""
    assert is_manual_ticket(["manual"]) is True
    assert is_manual_ticket(["bug", "manual", "ui"]) is True


def test_is_manual_ticket_false_when_label_absent() -> None:
    """No 'manual' label yields False."""
    assert is_manual_ticket([]) is False
    assert is_manual_ticket(["bug", "ai-agent"]) is False


def test_is_manual_ticket_case_sensitive() -> None:
    """Membership is case-sensitive (Jira labels are stored verbatim)."""
    assert is_manual_ticket(["Manual"]) is False
    assert is_manual_ticket(["MANUAL"]) is False


def test_is_manual_ticket_accepts_any_iterable() -> None:
    """A tuple / generator / set works (the param is Iterable[str])."""
    assert is_manual_ticket(("manual",)) is True
    assert is_manual_ticket({"manual"}) is True
    assert is_manual_ticket(iter(["x", "manual"])) is True


# ---------------------------------------------------------------------------
# upstream_has_manual — stub DAG + offset map
# ---------------------------------------------------------------------------


def _dag_manual_upstream() -> dict[str, tuple[str, list[str]]]:
    """SFP-1 (manual) <- SFP-2 (ai) <- SFP-3 (ai): SFP-3's upstream is manual.

    Deps point upstream (SFP-3 depends on SFP-2 depends on SFP-1).
    """
    return {
        "SFP-1": ("manual", []),
        "SFP-2": ("ai-agent", ["SFP-1"]),
        "SFP-3": ("ai-agent", ["SFP-2"]),
        "SFP-4": ("ai-agent", ["SFP-3"]),
    }


def _dag_all_ai() -> dict[str, tuple[str, list[str]]]:
    """An all-ai chain: SFP-2 <- SFP-3 <- SFP-4, no manual anywhere upstream."""
    return {
        "SFP-2": ("ai-agent", []),
        "SFP-3": ("ai-agent", ["SFP-2"]),
        "SFP-4": ("ai-agent", ["SFP-3"]),
    }


def _offset_map() -> dict[str, str]:
    """Jira key -> DOC key for the stub tickets (offset +17)."""
    return {
        _JIRA_SFP_1: "SFP-1",
        _JIRA_SFP_2: "SFP-2",
        _JIRA_SFP_3: "SFP-3",
        _JIRA_SFP_4: "SFP-4",
    }


def test_upstream_has_manual_true_for_manual_upstream() -> None:
    """A 👤 ticket transitive-upstream of an ai ticket -> True."""
    # SFP-3 (ai) depends on SFP-2 (ai) depends on SFP-1 (manual).
    dag = _dag_manual_upstream()
    assert upstream_has_manual(_JIRA_SFP_3, dag, _offset_map()) is True


def test_upstream_has_manual_true_for_direct_manual_dep() -> None:
    """A direct 👤 dependency is detected (depth-1)."""
    dag = _dag_manual_upstream()
    assert upstream_has_manual(_JIRA_SFP_2, dag, _offset_map()) is True


def test_upstream_has_manual_false_for_all_ai_chain() -> None:
    """An all-ai upstream chain -> False."""
    dag = _dag_all_ai()
    assert upstream_has_manual(_JIRA_SFP_4, dag, _offset_map()) is False


def test_upstream_has_manual_false_for_isolated_ai_ticket() -> None:
    """An ai ticket with no deps -> False."""
    dag = _dag_all_ai()
    assert upstream_has_manual(_JIRA_SFP_2, dag, _offset_map()) is False


def test_upstream_has_manual_excludes_ticket_itself() -> None:
    """The ticket's OWN executor is NOT consulted (the label clause owns that).

    SFP-1 is manual in the DAG, but as the START ticket its own executor must not
    make upstream_has_manual True — there is nothing upstream of it.
    """
    dag = _dag_manual_upstream()
    assert upstream_has_manual(_JIRA_SFP_1, dag, _offset_map()) is False


def test_upstream_has_manual_false_for_post_blueprint_key() -> None:
    """A Jira key absent from the offset map (post-blueprint) -> False."""
    dag = _dag_manual_upstream()
    assert upstream_has_manual("SFP-999", dag, _offset_map()) is False


def test_upstream_has_manual_safe_against_cycle() -> None:
    """A cyclic DAG does not loop forever and resolves deterministically."""
    # SFP-2 <-> SFP-3 (mutual deps), SFP-3 is manual.
    cyclic: dict[str, tuple[str, list[str]]] = {
        "SFP-2": ("ai-agent", ["SFP-3"]),
        "SFP-3": ("manual", ["SFP-2"]),
    }
    offset = {_JIRA_SFP_2: "SFP-2", _JIRA_SFP_3: "SFP-3"}
    # SFP-2's upstream includes the manual SFP-3 -> True, without infinite loop.
    assert upstream_has_manual(_JIRA_SFP_2, cyclic, offset) is True


def test_upstream_has_manual_skips_missing_dep_key() -> None:
    """A dep key absent from the DAG is skipped (no raise)."""
    dag: dict[str, tuple[str, list[str]]] = {
        "SFP-2": ("ai-agent", ["SFP-GHOST", "SFP-3"]),
        "SFP-3": ("ai-agent", []),
    }
    offset = {_JIRA_SFP_2: "SFP-2", _JIRA_SFP_3: "SFP-3"}
    # SFP-GHOST is missing; the all-ai remainder -> False.
    assert upstream_has_manual(_JIRA_SFP_2, dag, offset) is False


# ---------------------------------------------------------------------------
# compute_at_frontier
# ---------------------------------------------------------------------------


def test_compute_at_frontier_manual_label_short_circuits() -> None:
    """A 👤-labeled ticket is frontier regardless of dag/offset (no disk read).

    Passing dag=None/jira_to_doc=None would attempt a disk load ONLY if the label
    clause were False; since the label is True the short-circuit avoids any file
    access (the default files may not exist in this environment).
    """
    assert compute_at_frontier("SFP-999", ["manual"]) is True


def test_compute_at_frontier_ai_with_manual_upstream_is_frontier() -> None:
    """An ai ticket depending (transitively) on a 👤 ticket is at the frontier."""
    dag = _dag_manual_upstream()
    assert compute_at_frontier(_JIRA_SFP_4, [], dag=dag, jira_to_doc=_offset_map()) is True


def test_compute_at_frontier_ai_all_ai_chain_not_frontier() -> None:
    """An ai ticket with an all-ai upstream is NOT at the frontier."""
    dag = _dag_all_ai()
    assert compute_at_frontier(_JIRA_SFP_4, [], dag=dag, jira_to_doc=_offset_map()) is False


def test_compute_at_frontier_post_blueprint_ai_not_frontier() -> None:
    """A post-blueprint ai ticket (absent from offset) is not frontier."""
    dag = _dag_manual_upstream()
    assert compute_at_frontier("SFP-250", [], dag=dag, jira_to_doc=_offset_map()) is False


def test_compute_at_frontier_label_wins_even_if_upstream_all_ai() -> None:
    """The label clause takes precedence over an all-ai upstream."""
    dag = _dag_all_ai()
    assert compute_at_frontier(_JIRA_SFP_4, ["manual"], dag=dag, jira_to_doc=_offset_map()) is True


def test_compute_at_frontier_is_pure_and_deterministic() -> None:
    """Equal injected inputs yield equal results."""
    dag = _dag_manual_upstream()
    a = compute_at_frontier(_JIRA_SFP_3, [], dag=dag, jira_to_doc=_offset_map())
    b = compute_at_frontier(_JIRA_SFP_3, [], dag=dag, jira_to_doc=_offset_map())
    assert a == b is True


# ---------------------------------------------------------------------------
# compute_at_frontier — fail-safe default load
# ---------------------------------------------------------------------------


def test_compute_at_frontier_fails_safe_on_missing_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """A missing default DAG / offset file logs a warning and falls back to the
    label clause only (here False, since the label is absent)."""
    from workspace_worker.workflow import frontier

    # Redirect the default paths to non-existent files inside tmp_path.
    monkeypatch.setattr(frontier, "_DEFAULT_BUILD_ORDER", tmp_path / "nope_order.json")
    monkeypatch.setattr(frontier, "_DEFAULT_JIRA_STATE", tmp_path / "nope_state.json")

    with caplog.at_level(logging.WARNING, logger="workspace_worker.workflow.frontier"):
        result = compute_at_frontier("SFP-67", [])
    assert result is False
    # A warning was emitted about the missing DAG (and offset) file.
    assert any("build_order" in rec.message for rec in caplog.records)


def test_compute_at_frontier_fails_safe_on_corrupt_dag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """A corrupt (invalid JSON) default DAG file fails safe to label-only."""
    from workspace_worker.workflow import frontier

    bad = tmp_path / "bad_order.json"
    bad.write_text("{ not valid json", encoding="utf-8")
    monkeypatch.setattr(frontier, "_DEFAULT_BUILD_ORDER", bad)
    monkeypatch.setattr(frontier, "_DEFAULT_JIRA_STATE", tmp_path / "nope_state.json")

    with caplog.at_level(logging.WARNING, logger="workspace_worker.workflow.frontier"):
        assert compute_at_frontier("SFP-67", []) is False


def test_compute_at_frontier_fails_safe_on_corrupt_offset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """A valid DAG but corrupt offset file fails safe to label-only."""
    from workspace_worker.workflow import frontier

    order = tmp_path / "order.json"
    order.write_text(json.dumps({"tickets": []}), encoding="utf-8")
    bad_state = tmp_path / "bad_state.json"
    bad_state.write_text("not json at all", encoding="utf-8")
    monkeypatch.setattr(frontier, "_DEFAULT_BUILD_ORDER", order)
    monkeypatch.setattr(frontier, "_DEFAULT_JIRA_STATE", bad_state)

    # Label absent -> False (offset load failed safe).
    assert compute_at_frontier("SFP-67", []) is False


# ---------------------------------------------------------------------------
# load_build_order_dag / load_jira_to_doc (file shape parsing)
# ---------------------------------------------------------------------------


def test_load_build_order_dag_parses_tickets(tmp_path: Any) -> None:
    """The DAG loader normalizes the build_order.json ticket list."""
    f = tmp_path / "order.json"
    f.write_text(
        json.dumps(
            {
                "tickets": [
                    {"ticket": "SFP-1", "executor": "manual", "deps": []},
                    {"ticket": "SFP-2", "executor": "ai-agent", "deps": ["SFP-1"]},
                ]
            }
        ),
        encoding="utf-8",
    )
    dag = load_build_order_dag(f)
    assert dag == {
        "SFP-1": ("manual", []),
        "SFP-2": ("ai-agent", ["SFP-1"]),
    }


def test_load_build_order_dag_degrades_missing_fields(tmp_path: Any) -> None:
    """Missing executor/deps degrade to ''/[]; non-str entries are skipped."""
    f = tmp_path / "order.json"
    f.write_text(
        json.dumps(
            {
                "tickets": [
                    {"ticket": "SFP-1"},  # no executor, no deps
                    {"ticket": "SFP-2", "executor": "ai-agent", "deps": [1, "SFP-1"]},
                    {"executor": "manual"},  # no ticket key -> skipped
                    "not-a-dict",  # skipped
                ]
            }
        ),
        encoding="utf-8",
    )
    dag = load_build_order_dag(f)
    assert dag == {
        "SFP-1": ("", []),
        "SFP-2": ("ai-agent", ["SFP-1"]),  # the non-str dep '1' dropped
    }


def test_load_jira_to_doc_inverts_created_map(tmp_path: Any) -> None:
    """The offset loader inverts created: {doc: jira} -> {jira: doc}."""
    f = tmp_path / "state.json"
    f.write_text(
        json.dumps({"created": {"SFP-1": "SFP-18", "SFP-2": "SFP-19"}}),
        encoding="utf-8",
    )
    assert load_jira_to_doc(f) == {"SFP-18": "SFP-1", "SFP-19": "SFP-2"}


def test_load_jira_to_doc_empty_when_created_missing(tmp_path: Any) -> None:
    """A state file without a 'created' map yields {} (no raise)."""
    f = tmp_path / "state.json"
    f.write_text(json.dumps({"epics_created": []}), encoding="utf-8")
    assert load_jira_to_doc(f) == {}


@pytest.mark.parametrize("loader", [load_build_order_dag, load_jira_to_doc])
def test_loaders_raise_on_missing_file(loader: Any, tmp_path: Any) -> None:
    """The loaders surface OSError for a missing file (callers fail-safe)."""
    with pytest.raises(OSError):
        loader(tmp_path / "does-not-exist.json")
