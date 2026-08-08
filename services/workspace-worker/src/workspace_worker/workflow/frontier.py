"""Deterministic *frontier* detection for the readiness rubric (SFP-232).

The calibrated readiness rubric
(:func:`workspace_worker.workflow.readiness_rubric.evaluate_readiness_rubric`)
requires the two **boundary** ID-070 sections (``context_outputs_required_inputs``,
``dependencies``) as **presence only** when the ticket sits at the
human/automatic frontier. This module computes that boolean — *deterministically*
from the ticket's Jira labels plus the offline ticket DAG, with **no network and
no model** (layer-1 must stay deterministic — ID-064).

The frontier definition (ID-070 / SFP-232):

    at_frontier(T) = T itself is 👤 manual
                  OR any upstream dependency of T is 👤 manual

- "T itself is manual" is read off the Jira ``manual`` label
  (:data:`MANUAL_LABEL`) — the same label the manual classifier consumes
  (:mod:`workspace_worker.workflow.manual_classifier`).
- "any upstream dependency is manual" is read off the offline ticket DAG
  (``docs/build_order.json``), keyed by **DOC#** (SFP-1..SFP-171). Jira keys
  (DOC# + 17) are translated to DOC# via the offset map in
  ``.jira_creation_state.json`` (``created``: ``{doc_key: jira_key}``, inverted).
  A ticket NOT in the offset map (a post-blueprint ticket, e.g. SFP-189+) has no
  👤 upstream in practice — every 👤 ticket is a blueprint ticket and lives in
  the DAG — so it returns ``False`` for the upstream clause.

Inputs are *injectable* (``dag`` / ``jira_to_doc`` keyword args on
:func:`compute_at_frontier`) so the function is unit-testable with a stub DAG +
offset map and does not require the real files. The default-load path reads the
files from disk **once** and fails safe: a missing / corrupt
``build_order.json`` or ``.jira_creation_state.json`` logs a warning and yields
``at_frontier = is_manual_ticket(labels)`` only (the label clause is independent
of the files).

Grounded in ID-070 (the boundary sections), ID-064 (layer-1 determinism), and
SFP-232 (the calibration).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from pathlib import Path
from typing import Any

__all__ = [
    "MANUAL_LABEL",
    "AI_AGENT_LABEL",
    "is_manual_ticket",
    "upstream_has_manual",
    "compute_at_frontier",
    "load_build_order_dag",
    "load_jira_to_doc",
]

#: The executor label marking a 👤 (human) ticket. Jira ``fields.labels`` carries
#: this when a ticket is executed by a person. The readiness frontier treats a
#: 👤 ticket (and anything downstream of one) as the human/automatic boundary.
MANUAL_LABEL: str = "manual"

#: The executor label marking an 🤖 (AI-agent) ticket — the complement of
#: :data:`MANUAL_LABEL` in the DAG's ``executor`` field. Exposed for symmetry /
#: reference; the frontier check only looks for :data:`MANUAL_LABEL`.
AI_AGENT_LABEL: str = "ai-agent"

#: Module logger — default-load fail-safe warnings land here.
_log = logging.getLogger(__name__)

#: The monorepo root, resolved from this file's location. The module lives at
#: ``services/workspace-worker/src/workspace_worker/workflow/frontier.py`` so
#: ``parents[5]`` is the repo root (parents: 0=workflow, 1=workspace_worker,
#: 2=src, 3=workspace-worker, 4=services, 5=repo-root).
_REPO_ROOT: Path = Path(__file__).resolve().parents[5]

#: Default path of the offline ticket DAG (``docs/build_order.json``), keyed by
#: DOC#. Carries ``executor`` (``"manual"`` / ``"ai-agent"``) and ``deps`` per
#: ticket.
_DEFAULT_BUILD_ORDER: Path = _REPO_ROOT / "docs" / "build_order.json"

#: Default path of the Jira creation-state file (``.jira_creation_state.json``),
#: gitignored (local-only). Its ``created`` map is ``{doc_key: jira_key}``
#: (doc N -> jira N+17); inverted to ``{jira_key: doc_key}`` for the offset
#: translation. Its absence is benign (post-blueprint-only environment) and
#: fails safe.
_DEFAULT_JIRA_STATE: Path = _REPO_ROOT / ".jira_creation_state.json"

#: A loaded offline ticket DAG: ``{doc_key: (executor, deps)}``. ``executor`` is
#: ``"manual"`` / ``"ai-agent"`` (or ``""`` if the field is absent); ``deps`` is
#: the list of upstream DOC# keys. Internal alias used by the loaders and
#: :func:`upstream_has_manual`; injectable into :func:`compute_at_frontier` from
#: tests.
_Dag = dict[str, tuple[str, list[str]]]


def is_manual_ticket(labels: Iterable[str]) -> bool:
    """Return ``True`` iff the Jira ``manual`` label is present on the ticket.

    The label (:data:`MANUAL_LABEL`) is the authoritative signal that a ticket
    is 👤-executed. The check is a plain membership test — case-sensitive,
    matching how Jira stores labels. Pure and deterministic.

    Args:
        labels: The ticket's Jira labels (``fields.labels``), any iterable of
            strings.

    Returns:
        ``True`` iff ``"manual"`` is among ``labels``.
    """
    return MANUAL_LABEL in labels


def load_build_order_dag(path: Path = _DEFAULT_BUILD_ORDER) -> _Dag:
    """Load the offline ticket DAG from ``build_order.json``.

    The file shape is ``{"tickets": [{"ticket": "SFP-1", "executor": "...",
    "deps": [...]}, ...]}``, keyed by **DOC#**. This loader normalizes it to
    ``{doc_key: (executor, deps)}`` — missing ``executor`` / ``deps`` degrade to
    ``""`` / ``[]`` rather than raising (defensive).

    Args:
        path: Path to ``build_order.json`` (defaults to the repo-root file).

    Returns:
        The DAG as ``{doc_key: (executor, deps)}``.

    Raises:
        OSError: If the file cannot be read.
        json.JSONDecodeError: If the file is not valid JSON.
    """
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    dag: _Dag = {}
    for entry in data.get("tickets", []):
        if not isinstance(entry, dict):
            continue
        doc_key = entry.get("ticket")
        if not isinstance(doc_key, str):
            continue
        executor = entry.get("executor", "")
        if not isinstance(executor, str):
            executor = ""
        deps_raw = entry.get("deps", [])
        deps = [d for d in deps_raw if isinstance(d, str)]
        dag[doc_key] = (executor, deps)
    return dag


def load_jira_to_doc(path: Path = _DEFAULT_JIRA_STATE) -> dict[str, str]:
    """Load and invert the Jira creation-state offset map.

    ``.jira_creation_state.json`` carries ``created``: ``{doc_key: jira_key}``
    (DOC# N -> Jira# N+17). This inverts it to ``{jira_key: doc_key}`` so the
    DAG (keyed by DOC#) can be traversed starting from a Jira key.

    Args:
        path: Path to ``.jira_creation_state.json`` (defaults to the repo-root
            file).

    Returns:
        The inverted offset map ``{jira_key: doc_key}``.

    Raises:
        OSError: If the file cannot be read.
        json.JSONDecodeError: If the file is not valid JSON.
    """
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    created = data.get("created", {})
    if not isinstance(created, dict):
        return {}
    return {
        jira_key: doc_key
        for doc_key, jira_key in created.items()
        if isinstance(doc_key, str) and isinstance(jira_key, str)
    }


def upstream_has_manual(jira_key: str, dag: _Dag, jira_to_doc: dict[str, str]) -> bool:
    """Return ``True`` iff any *upstream* (transitive dep) ticket is 👤 manual.

    The ticket **itself** is excluded from the check — its own manual status is
    covered by the label clause of :func:`compute_at_frontier`. This function
    answers only the "any upstream dependency is manual" clause: a depth-first
    walk over the ticket's transitive ``deps`` in the DAG, returning ``True`` if
    any reachable upstream ticket has ``executor == "manual"``.

    Fail-safe assumptions (deterministic, no network):
    - If ``jira_key`` is not in the offset map (a post-blueprint ticket, e.g.
      SFP-189+), return ``False`` — every 👤 ticket is a blueprint ticket and
      lives in the DAG, so such a ticket has no 👤 upstream in practice.
    - A dep key absent from the DAG is skipped (no raise).
    - Cycles are guarded by a ``visited`` set (DAGs should be acyclic, but the
      guard makes the walk total regardless).

    Args:
        jira_key: The ticket's Jira key (e.g. ``"SFP-67"``).
        dag: The offline DAG (``{doc_key: (executor, deps)}``), as produced by
            :func:`load_build_order_dag`.
        jira_to_doc: The inverted offset map (``{jira_key: doc_key}``), as
            produced by :func:`load_jira_to_doc`.

    Returns:
        ``True`` iff any transitive upstream dependency has executor
        ``"manual"``; ``False`` otherwise (including when the ticket is not in
        the offset map).
    """
    doc_key = jira_to_doc.get(jira_key)
    if doc_key is None:
        # Post-blueprint ticket (not in the offset map) — no 👤 upstream.
        return False

    # Seed the walk with the ticket's DIRECT deps (the ticket itself is excluded
    # — its own manual status is the label clause's responsibility).
    start_entry = dag.get(doc_key)
    stack: list[str] = list(start_entry[1]) if start_entry is not None else []
    visited: set[str] = set()

    while stack:
        dep = stack.pop()
        if dep in visited:
            continue  # cycle guard
        visited.add(dep)
        entry = dag.get(dep)
        if entry is None:
            continue  # missing dep key — skip
        executor, deps = entry
        if executor == MANUAL_LABEL:
            return True
        stack.extend(deps)

    return False


def compute_at_frontier(
    jira_key: str,
    labels: Iterable[str],
    *,
    dag: _Dag | None = None,
    jira_to_doc: dict[str, str] | None = None,
) -> bool:
    """Compute whether a ticket sits at the human/automatic frontier.

    ``at_frontier(T) = is_manual_ticket(labels) or upstream_has_manual(T)``.

    The label clause is checked first and short-circuits (a 👤 ticket is the
    frontier regardless of its upstream). The upstream clause walks the offline
    DAG.

    ``dag`` / ``jira_to_doc`` are injectable for unit-testing with stubs. When
    either is ``None`` (the production path), it is loaded from its default
    file. The default-load fails safe: a missing / corrupt ``build_order.json``
    or ``.jira_creation_state.json`` logs a warning and the function returns
    ``is_manual_ticket(labels)`` only — layer-1 readiness stays deterministic
    and never raises on a file-system hiccup (the offset file is gitignored and
    may be legitimately absent in some environments).

    Args:
        jira_key: The ticket's Jira key (e.g. ``"SFP-67"``).
        labels: The ticket's Jira labels (``fields.labels``).
        dag: Optional pre-loaded offline DAG (for tests). If ``None``, loaded
            from :data:`_DEFAULT_BUILD_ORDER`.
        jira_to_doc: Optional pre-loaded inverted offset map (for tests). If
            ``None``, loaded from :data:`_DEFAULT_JIRA_STATE`.

    Returns:
        ``True`` iff the ticket is at the human/automatic frontier.
    """
    if is_manual_ticket(labels):
        return True

    # Load only what is needed; each file fails safe independently. The module
    # globals are read HERE (call time) — not via the loader default args — so a
    # caller/test may monkeypatch them (default args bind once at def time).
    resolved_dag: _Dag
    if dag is not None:
        resolved_dag = dag
    else:
        try:
            resolved_dag = load_build_order_dag(_DEFAULT_BUILD_ORDER)
        except (OSError, json.JSONDecodeError) as exc:
            _log.warning(
                "frontier: could not load build_order DAG (%s); "
                "falling back to label-only frontier check for %s",
                exc,
                jira_key,
            )
            return False  # label clause already False here

    resolved_map: dict[str, str]
    if jira_to_doc is not None:
        resolved_map = jira_to_doc
    else:
        try:
            resolved_map = load_jira_to_doc(_DEFAULT_JIRA_STATE)
        except (OSError, json.JSONDecodeError) as exc:
            _log.warning(
                "frontier: could not load Jira offset map (%s); "
                "falling back to label-only frontier check for %s",
                exc,
                jira_key,
            )
            return False

    return upstream_has_manual(jira_key, resolved_dag, resolved_map)
