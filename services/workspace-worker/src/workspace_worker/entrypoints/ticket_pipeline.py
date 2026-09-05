"""Composition root + thin orchestrator — the vertical slice (SFP-224).

This module is the **first end-to-end vertical slice** of the Workspace Worker:
it wires every landed piece (JiraClient, RepoManager, Worktree/Branch managers,
the four agent evaluators + their ClaudeAgentRuntime seam, the Local Execution
Engine, and the Git Provider Adapter) into ONE linear, fail-fast pipeline that
takes a ticket from "fetched" to "merged + Done".

Two halves (MAS §9.6 — the composition root owns all REAL construction; the loop
owns none):

* :func:`run_pipeline` — a **pure** orchestrator loop. Every collaborator is a
  keyword argument; the loop constructs nothing. It executes the linear pipeline
  in the exact order pinned by the tests and fails fast at every pre-merge gate.
* :func:`build` — the **composition-root factory**. Constructs the REAL
  collaborators from env + :class:`WorkspaceWorkerSettings` (no fakes). Tests
  monkeypatch these constructors with recording fakes; :func:`main` calls
  :func:`build` then :func:`run_pipeline`.

Binding resolutions (Orchestrator-decided; implemented exactly):

1. **Review verdict** is read off ``ReviewerOutput.review_status`` (the
   ``ReviewStatus`` enum) — NOT an ``event`` field. The ``event`` string is the
   ``GitProviderAdapter.submit_review`` concern; this loop maps
   ``APPROVED``→``"APPROVE"`` and everything else→``"REQUEST_CHANGES"``.
2. **Dual adapters** — ``coder_adapter`` (Coder token / ``sfp-coder-bot``) for
   ``create_pr`` + ``merge_pr``; ``reviewer_adapter`` (Reviewer token /
   ``sfp-reviewer-bot``) for ``submit_review`` (ID-073).
3. **Coder runtime ``cwd`` is set at LOOP time**, not build() time — the
   worktree path only exists after :meth:`WorktreeManager.add` runs. ``build()``
   constructs the Coder runtime with ``cwd=None``; the loop rebinds its cwd to
   the worktree path before calling :func:`code`.
4. **Object upload via** :meth:`RepoManager.push` — NEVER
   :meth:`GitProviderAdapter.push_branch` (the Git Data refs API cannot upload
   locally-committed objects).
5. **Single-PR semantics** — ``PlannerOutput.pr_specs[0]`` only (linear slice,
   no fan-out); more than one spec is a deterministic error.
6. **Per-role** ``max_turns``: planner=5, test_designer=5, coder=50, reviewer=5.
   One runtime per role; readiness reuses the planner runtime.
7. **Truthful checkpoints (SFP-247)** — ``code.json`` is written only after the
   pipeline's own commit verification (``git rev-list --count <base>..<branch>
   >= 1``, using the loop's existing ``branch_name``/``base_branch``
   bookkeeping) — never on model-run completion. At ``--resume`` entry every
   checkpoint is validated against reality before being trusted (same
   validation family as SFP-239's worktree check); an invalidated checkpoint is
   deleted with a logged reason and its stage re-runs, a trusted one logs a
   trusted line. Checks are cheap + deterministic: one git rev-list + JSON
   parse; no network/model calls.

No retry / queue / multi-PR fan-out (fail-fast linear loop). The end-to-end
"real ticket executed by the SFP runtime" acceptance criterion is an
operator-run smoke against the live stack, NOT a CI test (PRSpec risk).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError
from sfp_agent_runtime.interfaces import AgentRuntime, PromptProvider
from sfp_agent_runtime.prompt_builder import PromptBuilder
from sfp_config import LocalSecretProvider, SecretRef
from sfp_contracts.agents.coder import CoderOutput
from sfp_contracts.agents.planner import PlannerOutput, PrSpec
from sfp_contracts.agents.readiness import ReadinessVerdict
from sfp_contracts.agents.reviewer import ReviewerOutput, ReviewStatus
from sfp_contracts.agents.test_designer import TestDesignerOutput
from sfp_contracts.context.bindings import ResolvedContext
from sfp_contracts.context.declaration import TicketContextDeclaration

from workspace_worker.agent_runtime.model_config import AgentModelConfig
from workspace_worker.agent_runtime.runtime import ClaudeAgentRuntime
from workspace_worker.agents.coder import code
from workspace_worker.agents.planner import plan
from workspace_worker.agents.reviewer import review
from workspace_worker.agents.test_designer import design_tests

# SFP-248 — deterministic diff-surface test scoping for the Coder's INTERNAL
# cycles. Re-exported from this entrypoint so the Coder prompt can point at ONE
# import site (the pipeline module it already knows); the rule lives as
# data + code in the helper — never as prompt prose. Thin path per PRSpec.
from workspace_worker.entrypoints.test_scoping import (  # noqa: E402 — one documented import block after the workspace_worker.* block
    IMPORTER_MAP,
    TestScope,
    compute_test_scope,
)
from workspace_worker.exec.build import Runner
from workspace_worker.exec.build import build as _run_build
from workspace_worker.exec.lint import lint as _run_lint
from workspace_worker.exec.tests import run_tests as _run_tests
from workspace_worker.infrastructure.settings import WorkspaceWorkerSettings
from workspace_worker.repo.branch import BranchManager
from workspace_worker.repo.git.adapter import GitProviderAdapter, PullRequestReview
from workspace_worker.repo.jira.client import JiraClient
from workspace_worker.repo.manager import BaseSyncConflictError, RepoManager, RepoManagerError
from workspace_worker.repo.worktree import WorktreeManager, _sanitize_job_id
from workspace_worker.workflow.context_resolver import resolve_context
from workspace_worker.workflow.frontier import compute_at_frontier
from workspace_worker.workflow.readiness_gate import evaluate_readiness

__all__ = [
    "IMPORTER_MAP",
    "REVIEWER_MALFUNCTION_COMMENT",
    "REVIEWER_MALFUNCTION_ERROR",
    "PipelineDeps",
    "PipelineResult",
    "ReviewerMalfunctionError",
    "TestScope",
    "build",
    "compute_test_scope",
    "effective_review_state",
    "is_malformed_review",
    "main",
    "run_pipeline",
    "verify_branch_commits",
]

#: Jira transition id for "Done" (the only status transition the loop emits;
#: 31/41/51 are the workflow's ids — PRSpec risk). Applied AFTER a confirmed
#: merge, never before.
_DONE_TRANSITION_ID = "51"

#: The shared prompt-fragment directory shipped with the worker (``prompts/``
#: colocated with the ``agents/`` package). Every agent evaluator resolves its
#: default prompt against this dir via :class:`PromptBuilder`.
_DEFAULT_PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"

#: Per-role ``max_turns`` bound (RESOLUTION 6). Planner / Test Designer /
#: Reviewer are bounded tight (judgment runs); the Coder is given room for a
#: multi-step implementation run, capped to fail-fast.
_MAX_TURNS: Mapping[str, int] = {
    "planner": 15,
    "test_designer": 15,
    "coder": 50,
    "reviewer": 15,
    "readiness": 8,
}

#: SMOKE-PATCH (local, NOT committed): per-role reasoning effort forwarded to
#: the runtime -> ClaudeAgentOptions(effort=...). Emit-JSON agents finalize in
#: few turns at "low"; the Coder keeps "medium" for real implementation work.
_ROLE_EFFORT: Mapping[str, str] = {
    "planner": "low",
    "test_designer": "low",
    "reviewer": "low",
    "readiness": "low",
    "coder": "medium",
}

#: Output contract per agent role (one :class:`ClaudeAgentRuntime` each).
_OUTPUT_CONTRACTS: Mapping[str, type[Any]] = {
    "planner": PlannerOutput,
    "test_designer": TestDesignerOutput,
    "coder": CoderOutput,
    "reviewer": ReviewerOutput,
    # SMOKE-PATCH (local, NOT committed — formalize via PR): readiness emits
    # ReadinessOutput, NOT PlannerOutput — reusing the planner runtime made the
    # contract validation fail (fail-closed -> NEEDS_CLARIFICATION).
    "readiness": __import__(
        "sfp_contracts.agents.readiness", fromlist=["ReadinessOutput"]
    ).ReadinessOutput,
}

#: Module logger — checkpoint load/skip events land here for resume observability.
_log = logging.getLogger(__name__)


class ReviewerMalfunctionError(Exception):
    """Raised when the reviewer returns a malformed verdict TWICE (SFP-249).

    A verdict without a rationale on ANY status is a REVIEWER_MALFUNCTION — an
    infrastructure issue with the reviewer itself, never a judgment about the
    code. The pipeline re-runs the review once; a second malformed verdict
    aborts with :data:`REVIEWER_MALFUNCTION_ERROR`, whose message is
    deliberately distinct from the ``"review not approved"`` code-verdict abort
    so the two failure families cannot be confused (MAS §8.8).
    """


def _checkpoint_dir(worktree_base: Path, ticket_key: str) -> Path:
    """Default checkpoints dir: ``<worktree_base>/checkpoints/<ticket>``.

    A SIBLING of the per-ticket worktree (not its parent) — the worktree path is
    ``<worktree_base>/<ticket>``, so a checkpoints dir nested under
    ``<worktree_base>/<ticket>/`` would collide with ``WorktreeManager.add``
    (which refuses to clobber an existing path). Kept OUTSIDE the worktree so a
    worktree mishap cannot lose the checkpoints. ``main()`` uses this default;
    tests inject their own dir via :func:`run_pipeline`'s ``checkpoints_dir``.
    """
    return worktree_base / "checkpoints" / ticket_key


def _write_checkpoint(checkpoints_dir: Path, stage: str, output: BaseModel) -> None:
    """Persist a stage's contract output as JSON (pydantic round-trippable).

    Writes ``<checkpoints_dir>/<stage>.json``. The payload is
    :meth:`BaseModel.model_dump_json`, which :func:`_load_checkpoint` re-validates
    via ``model_validate`` — a clean round-trip under the ``extra='forbid'``
    contracts.
    """
    checkpoints_dir.mkdir(parents=True, exist_ok=True)
    (checkpoints_dir / f"{stage}.json").write_text(output.model_dump_json())


def _load_checkpoint[T: BaseModel](checkpoints_dir: Path, stage: str, model: type[T]) -> T | None:
    """Load + validate a stage checkpoint; fail-closed on corruption.

    Returns the typed object on success, or ``None`` if the checkpoint is absent
    or corrupt. A corrupt/unreadable checkpoint (bad JSON, schema mismatch, or
    OS error) is DELETED so the stage re-runs cleanly from scratch rather than
    crashing the resume — the spec's "delete it and re-run that stage" rule.
    """
    path = checkpoints_dir / f"{stage}.json"
    if not path.exists():
        return None
    try:
        return model.model_validate(json.loads(path.read_text()))
    except (json.JSONDecodeError, ValidationError, OSError) as exc:
        _log.warning(
            "checkpoint invalidated: %s (reason: %s); deleting — stage %s will re-run",
            path,
            _invalid_reason(exc),
            stage,
        )
        path.unlink(missing_ok=True)
        return None


def _invalid_reason(exc: Exception) -> str:
    """Map a checkpoint load/validate failure to a short logged reason string."""
    if isinstance(exc, json.JSONDecodeError):
        return "unparseable JSON (truncated/corrupt)"
    if isinstance(exc, ValidationError):
        return "schema mismatch (missing/extra contract keys)"
    return f"OS error: {exc}"


#: Signature of the injectable git runner used by the checkpoint validation
#: family (defaults to :func:`subprocess.run`; the shape matches the runners of
#: ``repo.manager`` / ``repo.worktree`` so tests inject the same kind of fake).
GitRunner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]


def _default_git_runner(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    """Run git with captured output (stderr never escapes unstructured)."""
    return subprocess.run(cmd, check=True, capture_output=True, text=True)


def verify_branch_commits(
    worktree_path: Path,
    branch: str,
    base: str,
    *,
    runner: GitRunner | None = None,
) -> int:
    """Count the ticket branch's commits relative to ``base`` (SFP-247).

    Runs exactly one ``git -C <worktree> rev-list --count <base>..<branch>``
    — the same cheap, deterministic probe the PRSpec pins. The refs are
    branch/base names already carried by the pipeline (``branch_name`` /
    ``base_branch``); nothing is guessed. No network, no model calls.

    Args:
        worktree_path: The per-job worktree (the clone's object database holds
            both refs — worktrees share the main repo's objects).
        branch: The ticket branch name (the PR head).
        base: The base branch name (the PR base / worktree base).
        runner: Injectable git executor (defaults to ``subprocess.run``).

    Returns:
        The commit count. A failed probe (missing ref, git error) returns
        ``-1`` — an *unknown* count, never a false "0 commits", so callers
        treat it conservatively (the checkpoint is invalidated and the stage
        re-runs; a lying "0" would wrongly discard a valid checkpoint).
    """
    run = runner or _default_git_runner
    try:
        result = run(
            [
                "git",
                "-C",
                str(worktree_path),
                "rev-list",
                "--count",
                f"{base}..{branch}",
            ]
        )
    except subprocess.CalledProcessError:
        return -1
    try:
        return int((result.stdout or "").strip())
    except ValueError:
        return -1


def _validate_checkpoints(
    checkpoints_dir: Path,
    *,
    worktree_path: Path,
    branch_name: str,
    base_branch: str,
    runner: GitRunner | None = None,
) -> None:
    """Resume-entry validation pass — prove every checkpoint against reality.

    Runs once at ``--resume`` entry, in the same validation family as SFP-239's
    worktree-existence check, BEFORE any checkpoint is trusted:

    * ``code.json`` — after the pydantic round-trip (a corrupt file is already
      deleted by :func:`_load_checkpoint`), the checkpoint's claim is checked
      against git itself: ``git rev-list --count <base>..<branch> >= 1``. A
      code.json with zero branch commits is the exact lie SFP-247 fixes (the
      pre-fix pipeline wrote it on model-run completion, before any commit
      existed). It is deleted with a logged reason and the code stage re-runs.
      An unresolvable probe (``-1``) also invalidates — fail-closed, never a
      false "0 commits".
    * ``plan.json`` / ``design.json`` — parseable JSON whose top-level keys
      match the expected contract (``pr_specs`` / ``pr_spec_id`` +
      ``test_plan``). A truncated/corrupt file is deleted with a logged reason
      and its stage re-runs. (Contract-key checking here is cheap and avoids
      the full pydantic re-validation being the ONLY corruption signal.)

    A **trusted** line is logged for every checkpoint that passes — every
    validation decision (trusted / invalidated+reason) is an auditable run-log
    fact (MAS §8.8).

    Deliberately NOT validated: a *valid* checkpoint is never re-entered —
    this pass deletes lying checkpoints so their stage re-runs; it creates no
    re-entry path for checkpoints that hold.
    """
    # design.json / plan.json — parse + expected contract keys. The exact
    # expected top-level keys come from the contracts themselves (the set of
    # REQUIRED fields), so a truncated-but-parseable file with the wrong keys
    # is caught here rather than surviving to a mid-resume schema failure.
    _stage_specs: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("design", ("pr_spec_id", "test_plan")),
        ("plan", ("pr_specs",)),
    )
    for stage, required_keys in _stage_specs:
        path = checkpoints_dir / f"{stage}.json"
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text())
            # The isinstance guard runs FIRST: a parseable-but-non-object file
            # (e.g. `42`, `"x"`) would raise TypeError from the membership
            # test, which this except clause does not catch.
            if not isinstance(payload, dict):
                raise ValueError("not a JSON object")
            missing = [k for k in required_keys if k not in payload]
            if missing:
                raise ValueError(f"missing expected contract keys: {missing}")
        except (json.JSONDecodeError, ValueError, OSError) as exc:
            _log.warning(
                "checkpoint invalidated: %s (reason: %s); deleting — stage %s will re-run",
                path,
                exc,
                stage,
            )
            path.unlink(missing_ok=True)
            continue
        _log.info("checkpoint trusted: %s (contract keys present, JSON parseable)", path)

    # code.json — the branch-commits check. Absent file: nothing to validate.
    code_path = checkpoints_dir / "code.json"
    if not code_path.exists():
        return
    count = verify_branch_commits(worktree_path, branch_name, base_branch, runner=runner)
    if count >= 1:
        _log.info(
            "checkpoint trusted: %s (branch %s has %d commit(s) vs base %s)",
            code_path,
            branch_name,
            count,
            base_branch,
        )
        return
    reason = (
        f"branch {branch_name} has 0 commits vs base {base_branch} (checkpoint written "
        "before the Coder's commits landed)"
        if count == 0
        else f"could not count commits on branch {branch_name} vs base {base_branch} "
        "(probe failed — unknown, treated as invalid)"
    )
    _log.warning(
        "checkpoint invalidated: %s (reason: %s); deleting — stage code will re-run",
        code_path,
        reason,
    )
    code_path.unlink(missing_ok=True)


@dataclass(frozen=True, slots=True)
class PipelineResult:
    """Outcome of :func:`run_pipeline`.

    Attributes:
        success: ``True`` iff the ticket reached a merged + Done state.
        pr_number: The pull-request number once :meth:`GitProviderAdapter.create_pr`
            succeeded; ``None`` if the loop aborted before PR creation.
        error: A descriptive message on a fail-fast abort; ``None`` on success.
        trace: The ordered tuple of pipeline step names actually executed — the
            linear ordering the tests assert. Steps after an abort are absent.
    """

    success: bool
    pr_number: int | None
    error: str | None
    trace: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PipelineDeps:
    """The collaborators + derived kwargs :func:`build` hands to :func:`run_pipeline`.

    Every field is a keyword argument of :func:`run_pipeline` except
    :attr:`ticket_key`/``pr_title``/``pr_body`` style meta. The composition root
    (:func:`build`) owns all REAL construction; the pure loop consumes this.
    """

    jira: JiraClient
    repo_manager: RepoManager
    coder_adapter: GitProviderAdapter
    reviewer_adapter: GitProviderAdapter
    runtimes: Mapping[str, AgentRuntime]
    exec_runners: Mapping[str, Runner | None]
    prompt_provider: PromptProvider
    worktree_manager: WorktreeManager
    branch_manager: BranchManager
    owner: str
    repo_name: str
    repo_url: str
    clone_dest: Path
    worktree_base: Path
    branch_name: str
    base_branch: str = "main"
    done_transition_id: str = _DONE_TRANSITION_ID
    #: Injectable git executor for the SFP-247 checkpoint-validation family
    #: (defaults to real ``subprocess.run``; the composition root leaves it
    #: ``None`` — only tests inject a fake).
    git_runner: GitRunner | None = None
    extra: Mapping[str, str] = field(default_factory=dict)


def _abort(trace: list[str], pr_number: int | None, error: str) -> PipelineResult:
    """Build a fail-fast :class:`PipelineResult` carrying the partial trace."""
    return PipelineResult(success=False, pr_number=pr_number, error=error, trace=tuple(trace))


def _build_pr_body(ticket_key: str, pr_spec: PrSpec, coder_output: CoderOutput) -> str:
    """Assemble a deterministic PR body from the contracts (SFP-238).

    Replaces the bare ``JIRA: <url>`` default with a structured markdown body
    assembled from the PRSpec + CoderOutput fields. Deterministic — no model
    prose. Empty source fields omit their section (no empty headings). The
    closing ``JIRA:`` line preserves the PR format convention (ID-025).

    Args:
        ticket_key: The Jira issue key (e.g. ``SFP-238``).
        pr_spec: The PR-spec the Coder implemented (SFP-14).
        coder_output: The Coder's implementation evidence (SFP-15).

    Returns:
        The assembled markdown body string.
    """
    sections: list[str] = []
    # Summary — PRSpec title; the goal carries a one-line description.
    summary_lines = ["## Summary", "", f"**{pr_spec.title}**"]
    if pr_spec.goal.strip():
        summary_lines.append("")
        summary_lines.append(pr_spec.goal.strip())
    sections.append("\n".join(summary_lines))
    # Changes — the files the PRSpec said the Coder would touch.
    if pr_spec.likely_files_or_modules:
        changes_lines = ["## Changes", ""]
        changes_lines.extend(f"- `{f}`" for f in pr_spec.likely_files_or_modules)
        sections.append("\n".join(changes_lines))
    # Validation evidence — the Coder's recorded evidence lines.
    if coder_output.validation_evidence:
        ve_lines = ["## Validation evidence", ""]
        ve_lines.extend(f"- {line}" for line in coder_output.validation_evidence)
        sections.append("\n".join(ve_lines))
    # Known limitations — omit if the Coder reported none.
    if coder_output.known_limitations:
        kl_lines = ["## Known limitations", ""]
        kl_lines.extend(f"- {line}" for line in coder_output.known_limitations)
        sections.append("\n".join(kl_lines))
    # JIRA convention line (required by the PR format — ID-025).
    sections.append(f"JIRA: https://arconta.atlassian.net/browse/{ticket_key}")
    return "\n\n".join(sections)


def effective_review_state(reviews: Sequence[PullRequestReview], head_sha: str) -> str:
    """Compute the PR's effective review state for ``head_sha`` (SFP-241).

    Mirrors GitHub's branch-protection semantics as observable from the reviews
    list: a review counts for the current head only when its ``commit_id``
    matches ``head_sha``; among those, the LATEST non-``PENDING`` review by list
    position decides — GitHub returns reviews oldest-first, so the last
    matching non-``PENDING`` entry is the most recent one.

    Decision table (``commit_id == head_sha``, non-``PENDING``, latest match):

    * ``APPROVED`` → ``"APPROVED"`` (a valid approval — do NOT re-review).
    * ``DISMISSED`` → ``"DISMISSED"`` (stale-dismissed approval; re-review).
    * ``CHANGES_REQUESTED`` / ``COMMENTED`` → that state (re-review).
    * No matching review at all → ``""`` (absent; re-review).

    ``COMMENTED`` is deliberately *not* treated as an approval — GitHub counts
    only ``APPROVED`` as satisfying a reviews-required rule, so a latest
    matching ``COMMENTED`` review means "not approved". ``PENDING`` reviews are
    skipped (they are not yet submitted and carry no verdict); a mismatched
    ``commit_id`` means the review predates the current head (stale). Fail
    closed: anything that is not a matching ``APPROVED`` yields a non-``APPROVED``
    value, so the caller's not-approve branch decides.

    Deterministic: a pure function of its arguments (no network, no clock).

    Args:
        reviews: The PR review records in GitHub list order (oldest first), as
            returned by :meth:`GitProviderAdapter.list_pr_reviews`.
        head_sha: The current head commit SHA of the PR.

    Returns:
        The effective review state string — ``"APPROVED"`` only when a review
        whose ``commit_id`` equals ``head_sha`` with state ``APPROVED`` is the
        latest non-``PENDING`` match; otherwise the latest matching state
        (``DISMISSED`` / ``CHANGES_REQUESTED`` / ``COMMENTED``) or ``""`` when
        no review matches the head.
    """
    effective = ""
    for entry in reviews:
        if entry.state == "PENDING":
            continue
        if entry.commit_id != head_sha:
            continue
        effective = entry.state
    return effective


#: SFP-249 — the differentiated abort message for a double reviewer
#: malfunction. Deliberately distinct from the ``"review not approved: ..."``
#: family (tests assert the difference) so an operator reading a failed run can
#: never misread an infrastructure failure as a code rejection (MAS §8.8: a
#: malfunction is a recorded fact, not a silent skip).
REVIEWER_MALFUNCTION_ERROR = (
    "reviewer malfunction: verdict without rationale twice (infra issue, not code issue)"
)


def is_malformed_review(review_output: ReviewerOutput) -> bool:
    """Whether a review verdict is a REVIEWER_MALFUNCTION (SFP-249).

    A verdict whose ``rationale`` is empty after strip is a malfunction on
    EVERY status — including ``APPROVED``. It is never acted on as a code
    verdict: the caller re-runs the review once and, on a second malfunction,
    aborts with the differentiated :data:`REVIEWER_MALFUNCTION_ERROR`.

    Pure/deterministic: a function of the verdict alone (no network, no clock).
    Belt-and-braces with the contract's own validator — the contract rejects an
    empty rationale at parse time, so in practice only an already-validated
    object built outside the normal seam can reach this check; the guard exists
    because the cost of misreading one as a code verdict is a wrong abort.

    Args:
        review_output: The verdict to classify.

    Returns:
        ``True`` when the verdict's rationale strips to empty (malfunction).
    """
    return not review_output.rationale.strip()


#: SFP-249 — the body of the issue-comment posted when a verdict comes back
#: malformed. Notes the malfunction AND that the review is being retried —
#: it is a comment on the PR conversation, never a review verdict.
REVIEWER_MALFUNCTION_COMMENT = (
    "⚠️ Reviewer malfunction: the reviewer returned a verdict without a rationale "
    "(infra issue, not a code verdict). Re-running the review once."
)


def _review_with_malfunction_guard(
    *,
    pr_spec: PrSpec,
    coder_output: CoderOutput,
    resolved: ResolvedContext,
    runtime: AgentRuntime,
    prompt_provider: PromptProvider,
    ticket_key: str,
    on_malfunction: Callable[[str], None] | None = None,
) -> ReviewerOutput:
    """Run a review and re-run it ONCE if the first verdict is malformed.

    SFP-249 guard around the reviewer seam, shared by BOTH review sites (the
    primary review and the SFP-241 pre-merge re-review):

    * first verdict malformed → notify via ``on_malfunction`` (the pipeline
      posts a PR COMMENT — never a review verdict) and re-run the SAME review
      call once (same inputs, same seam; the reviewer ``max_turns`` ceiling
      already applies at the runtime) and validate again;
    * a valid retry verdict is returned and acts normally downstream;
    * malformed twice → :class:`ReviewerMalfunctionError` carrying
      :data:`REVIEWER_MALFUNCTION_ERROR` — an infra abort, distinct from the
      ``"review not approved"`` code-verdict abort.

    Exactly ONE retry — bounded and deterministic; no loops.

    Args:
        on_malfunction: Optional sink invoked with
            :data:`REVIEWER_MALFUNCTION_COMMENT` when the FIRST verdict is
            malformed — the pipeline uses it to post the issue-comment (the
            malformed case must never surface as a review verdict). Not
            invoked again on the second malfunction: the abort message
            carries that fact.

    Raises:
        ReviewerMalfunctionError: when both attempts yield a malformed verdict.
    """
    review_output = review(
        pr_spec,
        coder_output,
        resolved,
        runtime=runtime,
        prompt_provider=prompt_provider,
        ticket_id=ticket_key,
    )
    if not is_malformed_review(review_output):
        return review_output

    _log.warning("reviewer returned a verdict without rationale — retrying review once")
    if on_malfunction is not None:
        on_malfunction(REVIEWER_MALFUNCTION_COMMENT)
    retry_output = review(
        pr_spec,
        coder_output,
        resolved,
        runtime=runtime,
        prompt_provider=prompt_provider,
        ticket_id=ticket_key,
    )
    if not is_malformed_review(retry_output):
        return retry_output

    _log.error("reviewer malfunction: verdict without rationale twice (infra issue)")
    raise ReviewerMalfunctionError(REVIEWER_MALFUNCTION_ERROR)


def _review_body(review_output: ReviewerOutput) -> str:
    """Compose the GitHub review body: status + rationale (SFP-249).

    Deterministic — no model prose, no clock. The status line is the verdict;
    the rationale summary is the reviewer's own recorded reason (single-line
    excerpt for the review body surface).

    Args:
        review_output: A NON-malformed verdict (caller-checked).

    Returns:
        The review body carrying both the status and the rationale.
    """
    status = review_output.review_status.value
    rationale = " ".join(review_output.rationale.split())
    return f"{status}: {rationale}"


def run_pipeline(
    ticket_key: str,
    *,
    jira: JiraClient,
    repo_manager: RepoManager,
    coder_adapter: GitProviderAdapter,
    reviewer_adapter: GitProviderAdapter,
    runtimes: Mapping[str, AgentRuntime],
    exec_runners: Mapping[str, Runner | None],
    prompt_provider: PromptProvider,
    worktree_manager: WorktreeManager,
    branch_manager: BranchManager,
    owner: str,
    repo_name: str,
    repo_url: str,
    clone_dest: Path,
    worktree_base: Path,
    branch_name: str,
    base_branch: str = "main",
    done_transition_id: str = _DONE_TRANSITION_ID,
    job_id: str | None = None,
    pr_title: str | None = None,
    pr_body: str | None = None,
    resume: bool = False,
    checkpoints_dir: Path | None = None,
    git_runner: GitRunner | None = None,
) -> PipelineResult:
    """Run the linear SFP pipeline for ``ticket_key`` to a merged + Done state.

    Pure: every collaborator is an injected kwarg; the loop constructs nothing.
    Executes the pipeline in the exact order pinned by the tests (see module
    docstring) and fails fast at every pre-merge gate — a not-READY verdict, a
    failing build/test/lint, or a non-APPROVED review aborts BEFORE merge and
    BEFORE the Done transition.

    Args:
        ticket_key: The Jira issue key (e.g. ``SFP-224``).
        jira: JiraClient (fetch_issue + transition).
        repo_manager: RepoManager (clone + push — the object-upload path).
        coder_adapter: GitProviderAdapter backed by the Coder token
            (``create_pr`` + ``merge_pr``).
        reviewer_adapter: GitProviderAdapter backed by the Reviewer token
            (``submit_review``).
        runtimes: Per-role agent runtimes keyed by ``planner`` /
            ``test_designer`` / ``coder`` / ``reviewer``. Readiness reuses the
            planner runtime. The Coder runtime's ``cwd`` is rebound to the
            worktree path at loop time (RESOLUTION 3).
        exec_runners: Per-op optional runners keyed by ``build`` / ``test`` /
            ``lint`` (``None`` → the exec module's real default runner).
        prompt_provider: Shared prompt provider fed to every evaluator.
        worktree_manager / branch_manager: Constructed on the (deterministic)
            clone dest so the loop stays construction-free.
        owner / repo_name / repo_url: GitHub repository coordinates + clone URL.
        clone_dest: The token-free clone destination (created by clone).
        worktree_base: Parent dir for the per-job worktree.
        branch_name / base_branch: The PR head / base branch names.
        done_transition_id: Jira "Done" transition id (default ``51``).
        job_id: Worktree job id (defaults to ``ticket_key``).
        pr_title / pr_body: Optional PR title/body overrides.
        resume: When ``True``, load existing stage checkpoints and SKIP the
            stages whose checkpoint is present (``plan`` / ``design`` / ``code``),
            restarting at the first missing stage; REUSE an existing worktree
            (clone + branch + Coder commits) instead of re-creating it. Readiness
            is never checkpointed and always re-runs. At resume entry every
            checkpoint is first VALIDATED against reality (SFP-247): ``code.json``
            requires >= 1 commit on the ticket branch vs base; ``plan.json`` /
            ``design.json`` must parse as JSON with their contract keys. A lying
            or corrupt checkpoint is deleted with a logged reason and its stage
            re-runs. A clean run (``resume=False``, the default) behaves EXACTLY
            as before — it still WRITES checkpoints after each checkpointed stage
            but never reads (or validates) them.
        checkpoints_dir: Where checkpoints live (injectable for tests). Defaults
            to ``<worktree_base>/<ticket_key>/checkpoints`` — kept OUTSIDE the
            worktree so a worktree mishap cannot lose them.
        git_runner: Injectable git executor for the checkpoint-validation family
            (``git rev-list --count <base>..<branch>``). Defaults to
            ``subprocess.run``; tests inject a fake so no real git spawns.

    Returns:
        The :class:`PipelineResult`. On success ``success=True``, ``pr_number`` is
        set, and the trace runs the full pipeline through merge + Done.
    """
    trace: list[str] = []

    # Resolve the checkpoints directory (injectable for tests; defaults to a
    # ticket-keyed subdir of worktree_base). Checkpoints are written on EVERY run
    # (clean or resume); they are only READ when ``resume`` is True.
    ckpt_dir = (
        checkpoints_dir
        if checkpoints_dir is not None
        else _checkpoint_dir(worktree_base, ticket_key)
    )
    # Resume-entry validation pass (SFP-247): before ANY checkpoint is trusted,
    # validate it against reality — code.json requires >=1 commit on the ticket
    # branch vs base; plan/design require parseable JSON with their contract
    # keys. A lying/truncated checkpoint is DELETED with a logged reason and its
    # stage re-runs (self-healing resume); a trusted one is logged as such.
    # Clean runs (resume=False) never read checkpoints — no validation either.
    worktree_job_id = job_id or ticket_key
    worktree_path = worktree_base / _sanitize_job_id(worktree_job_id)
    if resume:
        _validate_checkpoints(
            ckpt_dir,
            worktree_path=worktree_path,
            branch_name=branch_name,
            base_branch=base_branch,
            runner=git_runner,
        )

    # 1. Fetch the issue + parse its ADF description into a ParsedTicket.
    trace.append("jira.fetch_issue")
    issue = jira.fetch_issue(ticket_key)
    ticket = issue.parsed

    # Minimal/empty resolved context for this slice (PRSpec risk: the ticket
    # allows a minimal ResolvedContext; resolve_context over an empty
    # declaration yields exactly that — empty resolved + missing lists).
    declaration = TicketContextDeclaration()
    resolved: ResolvedContext = resolve_context(declaration, {}, ticket_id=ticket_key)

    # 2. Readiness gate — uses a DEDICATED readiness runtime (ReadinessOutput
    # contract), NOT the planner runtime (PlannerOutput) — contract mismatch
    # made the gate fail-closed (SMOKE-PATCH, local, formalize via PR).
    # SFP-232: compute the human/automatic frontier flag deterministically from
    # the issue's labels + the offline DAG, and forward it to the rubric so the
    # two *boundary* ID-070 sections are required (presence only) just at the
    # frontier. compute_at_frontier fails safe on a missing/corrupt DAG file.
    trace.append("evaluate_readiness")
    at_frontier = compute_at_frontier(ticket_key, issue.labels)
    readiness = evaluate_readiness(
        ticket,
        resolved,
        runtime=runtimes["readiness"],
        prompt_provider=prompt_provider,
        ticket_id=ticket_key,
        at_frontier=at_frontier,
    )
    if readiness.verdict is not ReadinessVerdict.READY:
        # Surface the WHY of a non-READY verdict (SFP-236): the verdict alone is
        # not actionable — the owner needs the blocking ambiguities, unresolved
        # inputs, and failed layer-1 rubric checks to enrich the ticket or judge
        # a false-positive (ID-065 / ID-064). Empty lists are omitted so a bare
        # verdict (e.g. MANUAL_REQUIRED with no detail) stays readable.
        detail: list[str] = []
        if readiness.blocking_ambiguities:
            detail.append("blocking_ambiguities: " + " || ".join(readiness.blocking_ambiguities))
        if readiness.missing_inputs:
            detail.append("missing_inputs: " + " || ".join(readiness.missing_inputs))
        failed_rubric = [name for name, passed in readiness.rubric_results.items() if not passed]
        if failed_rubric:
            detail.append("rubric_failed: " + ",".join(failed_rubric))
        msg = f"readiness gate not READY: {readiness.verdict.value}"
        if detail:
            msg += " — " + " ;; ".join(detail)
        return _abort(trace, None, msg)

    # 3. Plan — decompose the ticket into one or more PR-specs. On resume, a
    # valid ``plan`` checkpoint is loaded and the Planner run is SKIPPED (the
    # ~seconds planner is cheap, but skipping it keeps resume deterministic and
    # avoids re-decomposing a ticket whose downstream stages may already be done).
    planner_output: PlannerOutput | None = (
        _load_checkpoint(ckpt_dir, "plan", PlannerOutput) if resume else None
    )
    if planner_output is None:
        trace.append("plan")
        planner_output = plan(
            ticket,
            resolved,
            runtime=runtimes["planner"],
            prompt_provider=prompt_provider,
            ticket_id=ticket_key,
        )
        _write_checkpoint(ckpt_dir, "plan", planner_output)
    else:
        _log.info("resume: skipping plan stage (checkpoint present) for %s", ticket_key)
    # RESOLUTION 5 — single-PR slice: process pr_specs[0] only. More than one
    # spec is a deterministic error (no fan-out in this slice).
    if len(planner_output.pr_specs) != 1:
        return _abort(
            trace,
            None,
            f"linear slice expects exactly 1 PR-spec, got {len(planner_output.pr_specs)}",
        )
    pr_spec: PrSpec = planner_output.pr_specs[0]

    # 4. Design the test plan (drives the Coder's test writing downstream). On
    # resume, a valid ``design`` checkpoint is loaded and the Test Designer run
    # is SKIPPED. The output is captured (not discarded) so it is checkpointable.
    design_output: TestDesignerOutput | None = (
        _load_checkpoint(ckpt_dir, "design", TestDesignerOutput) if resume else None
    )
    if design_output is None:
        trace.append("design_tests")
        design_output = design_tests(
            ticket,
            resolved,
            runtime=runtimes["test_designer"],
            prompt_provider=prompt_provider,
            ticket_id=ticket_key,
        )
        _write_checkpoint(ckpt_dir, "design", design_output)
    else:
        _log.info("resume: skipping design stage (checkpoint present) for %s", ticket_key)

    # 5-6. Clone + worktree. On resume, if the worktree already exists it is
    # REUSED (clone + branch + the Coder's commits already present — the ``code``
    # checkpoint implies all three). We detect reuse by the worktree path's
    # existence; otherwise clone + ``worktree add -b <branch>`` as on a clean
    # run.
    if resume and worktree_path.exists():
        _log.info("resume: reusing existing worktree %s for %s", worktree_path, ticket_key)
    else:
        # 5. Clone the repo (token-free origin written by RepoManager.clone).
        trace.append("repo_manager.clone")
        repo_manager.clone(repo_url, clone_dest)

        # 6. Materialise an isolated per-job worktree off the clone, directly on
        # a NEW branch (branch_name) based off base_branch. Creating the worktree
        # with `-b` avoids colliding with the clone's checkout of base_branch
        # (main) and makes the separate create_branch/checkout unnecessary.
        trace.append("worktree.add")
        worktree = worktree_manager.add(
            worktree_job_id, base_branch, worktree_base, new_branch=branch_name
        )
        worktree_path = worktree.path

    # 8. Coder implements the PR-spec. RESOLUTION 3: the Coder runtime's cwd is
    #    set at LOOP time — the worktree path only exists after step 6. build()
    #    constructed the runtime with cwd=None; rebind it here. The runtime
    #    reads its `_cwd` when .run() builds options, so the rebind takes effect
    #    for this run. (Within the composition-root package boundary.)
    #    On resume, a valid ``code`` checkpoint is loaded and the Coder run (the
    #    expensive ~10-min stage) is SKIPPED — this is the primary cost lever.
    coder_output: CoderOutput | None = (
        _load_checkpoint(ckpt_dir, "code", CoderOutput) if resume else None
    )
    if coder_output is None:
        trace.append("code")
        coder_runtime: Any = runtimes["coder"]
        coder_runtime._cwd = str(worktree_path)
        coder_output = code(
            pr_spec,
            resolved,
            runtime=runtimes["coder"],
            prompt_provider=prompt_provider,
            ticket_id=ticket_key,
        )
        # SFP-247 — TRUTHFUL ORDERING: the code checkpoint is written ONLY
        # after the pipeline's own commit step is verified — the branch must
        # hold >= 1 commit vs base — never on model-run completion. A Coder
        # run that returned a contract output but committed nothing (crash
        # between run and commit, a model that skipped `git commit`) leaves NO
        # checkpoint, so a --resume re-enters code instead of skipping it on a
        # lie. A failed probe (git error) also leaves no checkpoint — the sync
        # below surfaces it rather than recording a lying checkpoint.
        commit_count = verify_branch_commits(
            worktree_path, branch_name, base_branch, runner=git_runner
        )
        if commit_count >= 1:
            _write_checkpoint(ckpt_dir, "code", coder_output)
        else:
            _log.warning(
                "code stage completed but branch %s has %s commit(s) vs base %s; "
                "code.json NOT written (checkpoint only after commits land)",
                branch_name,
                commit_count if commit_count >= 0 else "unknown",
                base_branch,
            )
    else:
        _log.info("resume: skipping code stage (checkpoint present) for %s", ticket_key)

    # 9. Pre-push base sync (SFP-240) — merge origin/<base_branch> into the
    #    ticket branch IN THE WORKTREE before any gate runs, so the gates below
    #    verify the POST-merge tree (the exact tree that will be pushed). Merge,
    #    never rebase — rebase would rewrite the Coder's commits. A conflict
    #    aborts fail-closed with the conflicted file names (the worktree is left
    #    pre-merge via `git merge --abort` inside sync_base); the operator
    #    resolves in the worktree and re-runs with --resume. The LOCAL-merge
    #    route (not GitProviderAdapter.sync_branch, SFP-59) is chosen because it
    #    surfaces named-file conflicts locally at push time and pushes a
    #    byte-identical verified tree; upload stays RepoManager.push (RESOLUTION 4).
    trace.append("repo_manager.sync_base")
    try:
        repo_manager.sync_base(worktree_path, base_branch)
    except BaseSyncConflictError as exc:
        # Fail-closed: no gates, no push, no PR against a conflicted tree. The
        # message carries the named files + the operator's recovery recipe.
        files = ", ".join(exc.conflicted_files) if exc.conflicted_files else "(none)"
        return _abort(
            trace,
            None,
            f"base stale: merge conflicts in {files}; "
            f"resolve in {worktree_path} and re-run with --resume",
        )
    except RepoManagerError as exc:
        return _abort(trace, None, f"base sync failed: {exc}")

    # 10-12. Local Execution Engine gates — build, tests, lint, run AFTER the
    #     base merge so they verify the merged tree. Each is fail-fast:
    #     a non-success aborts BEFORE push (no PR is opened against a red tree).
    trace.append("build")
    build_result = _run_build(worktree_path, runner=exec_runners["build"])
    if not build_result.success:
        return _abort(trace, None, f"build failed: exit_code={build_result.exit_code}")

    trace.append("run_tests")
    test_result = _run_tests(worktree_path, runner=exec_runners["test"])
    if not test_result.success:
        return _abort(
            trace,
            None,
            (
                f"tests failed: exit_code={test_result.exit_code}\n"
                f"--STDOUT tail--\n{test_result.stdout_tail[-1200:]}\n"
                f"--STDERR tail--\n{test_result.stderr_tail[-1200:]}"
            ),
        )

    trace.append("lint")
    lint_result = _run_lint(worktree_path, runner=exec_runners["lint"])
    if not lint_result.success:
        return _abort(
            trace,
            None,
            f"lint failed:\n{lint_result}",
        )

    # 13. Push the branch — RESOLUTION 4: object upload via RepoManager.push,
    #     NEVER GitProviderAdapter.push_branch (the Git Data refs API cannot
    #     upload locally-committed objects). Token is in-memory only. The tree
    #     pushed is the one the gates just verified (post base-sync, SFP-240).
    trace.append("repo_manager.push")
    repo_manager.push(worktree_path, branch_name)

    # 14. Open the pull request via the CODER adapter (sfp-coder-bot identity).
    trace.append("coder_adapter.create_pr")
    title = pr_title if pr_title is not None else f"{ticket_key}: {pr_spec.title}"
    body = pr_body if pr_body is not None else _build_pr_body(ticket_key, pr_spec, coder_output)
    pr = coder_adapter.create_pr(
        owner,
        repo_name,
        title=title,
        head=branch_name,
        base=base_branch,
        body=body,
    )

    # 15. Reviewer judges the PR. SFP-249: the seam is wrapped by the
    #     malfunction guard — a malformed verdict (empty rationale on ANY
    #     status) triggers a PR COMMENT (never a verdict) and is re-run ONCE;
    #     malformed twice raises ReviewerMalfunctionError, an infra abort
    #     distinct from a code verdict.
    trace.append("review")

    def _comment_malfunction(body: str) -> None:
        trace.append("reviewer_adapter.add_pr_comment")
        reviewer_adapter.add_pr_comment(owner, repo_name, pr.number, body=body)

    review_output: ReviewerOutput = _review_with_malfunction_guard(
        pr_spec=pr_spec,
        coder_output=coder_output,
        resolved=resolved,
        runtime=runtimes["reviewer"],
        prompt_provider=prompt_provider,
        ticket_key=ticket_key,
        on_malfunction=_comment_malfunction,
    )

    # RESOLUTION 1: branch on review_status (no `event` field on ReviewerOutput).
    # Map the verdict to the GitHub `event` string for submit_review, then merge
    # + transition Done ONLY on APPROVED. SFP-249: the body carries the status
    # PLUS the rationale (the verdict is never a bare status).
    event = "APPROVE" if review_output.review_status is ReviewStatus.APPROVED else "REQUEST_CHANGES"
    trace.append("reviewer_adapter.submit_review")
    reviewer_adapter.submit_review(
        owner,
        repo_name,
        pr.number,
        event=event,
        body=_review_body(review_output),
    )

    if review_output.review_status is not ReviewStatus.APPROVED:
        # Review did not approve — fail fast: NO merge, NO Done transition.
        return _abort(trace, pr.number, f"review not approved: {review_output.review_status.value}")

    # 15. PRE-MERGE REVIEW-STATE GATE (SFP-241). The same-run review above is
    #     necessary but not sufficient: a dismissal or a subsequent push can
    #     have invalidated it on GitHub's side. The API is the source of truth
    #     — read the PR's actual review state for the CURRENT head and only
    #     merge when it is APPROVED. This EXTENDS the non-APPROVED guard; it
    #     never bypasses it. Scope guard: this is NOT the SFP-122 rework loop —
    #     a non-APPROVED re-review aborts fail-closed, it does not loop.
    trace.append("coder_adapter.list_pr_reviews")
    pr_reviews = coder_adapter.list_pr_reviews(owner, repo_name, pr.number)
    state = effective_review_state(pr_reviews, pr.head_sha)

    if state != "APPROVED":
        # DISMISSED / stale (commit mismatch) / absent / CHANGES_REQUESTED —
        # the effective review for the current head is not an approval.
        # Self-heal: re-run the reviewer agent on the current head under the
        # REVIEWER identity (the reviewer runtime + reviewer_adapter are the
        # sfp-reviewer-bot seam, ID-073) and submit its verdict via the same
        # review + submit_review path as the run's normal review stage.
        _log.info(
            "pre-merge review state for PR %s is %r (head %s) — re-running reviewer",
            pr.number,
            state or "ABSENT",
            pr.head_sha,
        )
        trace.append("review")
        review_output = _review_with_malfunction_guard(
            pr_spec=pr_spec,
            coder_output=coder_output,
            resolved=resolved,
            runtime=runtimes["reviewer"],
            prompt_provider=prompt_provider,
            ticket_key=ticket_key,
            on_malfunction=_comment_malfunction,
        )
        event = (
            "APPROVE" if review_output.review_status is ReviewStatus.APPROVED else "REQUEST_CHANGES"
        )
        trace.append("reviewer_adapter.submit_review")
        reviewer_adapter.submit_review(
            owner,
            repo_name,
            pr.number,
            event=event,
            body=_review_body(review_output),
        )

        if review_output.review_status is not ReviewStatus.APPROVED:
            # Re-review did not approve — the existing fail-closed abort: NO
            # merge, NO Done transition.
            return _abort(
                trace,
                pr.number,
                f"review not approved: {review_output.review_status.value}",
            )

    # 16. Merge (squash) via the CODER adapter + transition the ticket to Done.
    trace.append("coder_adapter.merge_pr")
    coder_adapter.merge_pr(owner, repo_name, pr.number, merge_method="squash")
    trace.append("jira.transition")
    jira.transition(ticket_key, done_transition_id)

    return PipelineResult(success=True, pr_number=pr.number, error=None, trace=tuple(trace))


# ---------------------------------------------------------------------------
# Composition root — owns ALL real construction (no fakes here).
# ---------------------------------------------------------------------------


def _build_runtimes(
    settings: WorkspaceWorkerSettings,
    secret_provider: LocalSecretProvider,
    model_resolver: AgentModelConfig,
) -> dict[str, ClaudeAgentRuntime]:
    """Construct the four per-role ClaudeAgentRuntime instances (RESOLUTION 6).

    The Coder runtime is constructed with ``cwd=None`` — its cwd is rebound at
    loop time once the worktree path exists (RESOLUTION 3).
    """
    runtimes: dict[str, ClaudeAgentRuntime] = {}
    for role, contract in _OUTPUT_CONTRACTS.items():
        runtimes[role] = ClaudeAgentRuntime(
            settings,
            secret_provider,
            contract,
            max_turns=_MAX_TURNS[role],
            cwd=None,
            model_resolver=model_resolver,
            effort=_ROLE_EFFORT.get(role),
            # All roles get schema enforcement (output_format). The earlier
            # "Coder didn't write with output_format" was the permission prompt
            # (fixed via permission_mode=bypassPermissions in the runtime), NOT
            # output_format — so the Coder now both writes files AND emits a
            # clean CoderOutput JSON.
            enforce_schema=True,
        )
    return runtimes


def build(
    ticket_key: str,
    *,
    slug: str = "slice",
    owner: str | None = None,
    repo_name: str | None = None,
    worktree_base: Path | None = None,
    base_branch: str = "main",
    prompts_dir: Path | None = None,
    settings: WorkspaceWorkerSettings | None = None,
    secret_provider: LocalSecretProvider | None = None,
) -> PipelineDeps:
    """Composition-root factory: construct the REAL collaborators from env+CLI.

    Reads GitHub coordinates + tokens from the environment, the LLM endpoint /
    model / secret-ref from :class:`WorkspaceWorkerSettings`, and resolves the
    provider secret via :class:`LocalSecretProvider`. Constructs the dual GitHub
    adapters (Coder + Reviewer tokens — RESOLUTION 2 / ID-073), the four
    per-role runtimes, the JiraClient, RepoManager (Coder token — the object
    uploader is a Coder-side op, ID-035), and the Worktree/Branch managers on
    the deterministic clone dest.

    Tests monkeypatch the constructors (``ClaudeAgentRuntime``, ``JiraClient``,
    …) with recording fakes rather than calling this against the live stack.
    """
    owner = owner or os.environ.get("SFP_GIT_OWNER", "arconta")
    repo_name = repo_name or os.environ.get("SFP_GIT_REPO", "sfp")
    repo_url = os.environ.get("SFP_REPO_URL", f"https://github.com/{owner}/{repo_name}.git")

    worktree_base = worktree_base or Path(os.environ.get("SFP_WORKTREE_BASE", "/tmp/sfp-worktrees"))
    clone_dest = worktree_base / "clone"

    # Branch naming rule ``sfp-<ticket-key>-<slug>`` (ID-025 / BranchManager).
    # Inlined rather than read off the (possibly monkeypatched) BranchManager
    # class so the composition root's static helper stays patch-safe in tests.
    branch_name = f"sfp-{ticket_key}-{slug}".lower()

    # Typed worker settings + secret provider.
    if settings is None:
        # pydantic-settings loads required fields from env at runtime (env_prefix
        # "SFP_"); mypy sees them as required ctor args, so the call is annotated.
        settings = WorkspaceWorkerSettings()  # type: ignore[call-arg]
    if secret_provider is None:
        secret_provider = LocalSecretProvider(secrets_file=None)

    # GitHub tokens — Coder pushes/merges, Reviewer submits review (ID-073).
    # Resolved from env (the local provider reads env first); the refs are
    # opaque names, never the raw tokens.
    coder_token_ref = SecretRef(name="GITHUB_TOKEN_CODER")
    reviewer_token_ref = SecretRef(name="GITHUB_TOKEN_REVIEWER")
    coder_token = secret_provider.resolve(coder_token_ref)
    reviewer_token = secret_provider.resolve(reviewer_token_ref)

    # Jira credentials.
    jira_site = os.environ.get("SFP_JIRA_SITE", "https://arconta.atlassian.net")
    jira_email = os.environ.get("SFP_JIRA_EMAIL", "")
    jira_token = secret_provider.resolve(SecretRef(name="JIRA_API_TOKEN"))

    # Per-role model routing (planner/coder/reviewer overrides + default floor).
    model_resolver = AgentModelConfig(
        default_model=settings.default_model,
        planner=os.environ.get("SFP_AGENT_MODEL_PLANNER"),
        coder=os.environ.get("SFP_AGENT_MODEL_CODER"),
        reviewer=os.environ.get("SFP_AGENT_MODEL_REVIEWER"),
    )

    runtimes = _build_runtimes(settings, secret_provider, model_resolver)
    prompt_provider = PromptBuilder(prompts_dir or _DEFAULT_PROMPT_DIR)

    return PipelineDeps(
        jira=JiraClient(jira_site, jira_email, jira_token),
        repo_manager=RepoManager(coder_token),
        coder_adapter=GitProviderAdapter(coder_token),
        reviewer_adapter=GitProviderAdapter(reviewer_token),
        runtimes=runtimes,
        exec_runners={"build": None, "test": None, "lint": None},
        prompt_provider=prompt_provider,
        worktree_manager=WorktreeManager(clone_dest),
        branch_manager=BranchManager(clone_dest),
        owner=owner,
        repo_name=repo_name,
        repo_url=repo_url,
        clone_dest=clone_dest,
        worktree_base=worktree_base,
        branch_name=branch_name,
        base_branch=base_branch,
        done_transition_id=_DONE_TRANSITION_ID,
        git_runner=None,
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint: ``python -m workspace_worker.entrypoints.ticket_pipeline --ticket SFP-XXX``.

    Parses ``--ticket`` (required), an optional ``--slug`` (branch slug),
    ``--resume`` (skip stages whose checkpoint is present), and env-derived
    coordinates; calls :func:`build` then :func:`run_pipeline`, prints a one-line
    outcome, and returns a process exit code (``0`` on success).
    """
    parser = argparse.ArgumentParser(
        prog="workspace_worker.entrypoints.ticket_pipeline",
        description="Run one ticket through the SFP vertical slice.",
    )
    parser.add_argument("--ticket", required=True, help="Jira issue key, e.g. SFP-224")
    parser.add_argument("--slug", default="slice", help="Short branch slug (default: slice)")
    parser.add_argument("--base-branch", default="main", help="PR base branch")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from stage checkpoints at <worktree_base>/checkpoints/<ticket>/ "
        "(skip plan/design/code whose checkpoint is present; reuse an existing worktree).",
    )
    args = parser.parse_args(argv)

    deps = build(args.ticket, slug=args.slug, base_branch=args.base_branch)
    result = run_pipeline(
        args.ticket,
        jira=deps.jira,
        repo_manager=deps.repo_manager,
        coder_adapter=deps.coder_adapter,
        reviewer_adapter=deps.reviewer_adapter,
        runtimes=deps.runtimes,
        exec_runners=deps.exec_runners,
        prompt_provider=deps.prompt_provider,
        worktree_manager=deps.worktree_manager,
        branch_manager=deps.branch_manager,
        owner=deps.owner,
        repo_name=deps.repo_name,
        repo_url=deps.repo_url,
        clone_dest=deps.clone_dest,
        worktree_base=deps.worktree_base,
        branch_name=deps.branch_name,
        base_branch=deps.base_branch,
        done_transition_id=deps.done_transition_id,
        resume=args.resume,
        git_runner=deps.git_runner,
    )

    if result.success:
        print(f"slice ok: ticket={args.ticket} pr={result.pr_number}")  # noqa: T201
        return 0
    print(  # noqa: T201
        f"slice aborted: ticket={args.ticket} pr={result.pr_number} "
        f"error={result.error} trace={list(result.trace)}"
    )
    return 1


if __name__ == "__main__":  # pragma: no cover — module-as-script path
    sys.exit(main())
