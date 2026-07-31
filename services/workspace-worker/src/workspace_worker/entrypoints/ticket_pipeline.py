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

No retry / queue / multi-PR fan-out (fail-fast linear loop). The end-to-end
"real ticket executed by the SFP runtime" acceptance criterion is an
operator-run smoke against the live stack, NOT a CI test (PRSpec risk).
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sfp_agent_runtime.interfaces import AgentRuntime, PromptProvider
from sfp_agent_runtime.prompt_builder import PromptBuilder
from sfp_config import LocalSecretProvider, SecretRef
from sfp_contracts.agents.coder import CoderOutput
from sfp_contracts.agents.planner import PlannerOutput, PrSpec
from sfp_contracts.agents.readiness import ReadinessVerdict
from sfp_contracts.agents.reviewer import ReviewerOutput, ReviewStatus
from sfp_contracts.context.bindings import ResolvedContext
from sfp_contracts.context.declaration import TicketContextDeclaration

from workspace_worker.agent_runtime.model_config import AgentModelConfig
from workspace_worker.agent_runtime.runtime import ClaudeAgentRuntime
from workspace_worker.agents.coder import code
from workspace_worker.agents.planner import plan
from workspace_worker.agents.reviewer import review
from workspace_worker.agents.test_designer import design_tests
from workspace_worker.exec.build import Runner
from workspace_worker.exec.build import build as _run_build
from workspace_worker.exec.lint import lint as _run_lint
from workspace_worker.exec.tests import run_tests as _run_tests
from workspace_worker.infrastructure.settings import WorkspaceWorkerSettings
from workspace_worker.repo.branch import BranchManager
from workspace_worker.repo.git.adapter import GitProviderAdapter
from workspace_worker.repo.jira.client import JiraClient
from workspace_worker.repo.manager import RepoManager
from workspace_worker.repo.worktree import WorktreeManager
from workspace_worker.workflow.context_resolver import resolve_context
from workspace_worker.workflow.readiness_gate import evaluate_readiness

__all__ = ["PipelineDeps", "PipelineResult", "build", "main", "run_pipeline"]

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
    "planner": 5,
    "test_designer": 5,
    "coder": 50,
    "reviewer": 5,
}

#: Output contract per agent role (one :class:`ClaudeAgentRuntime` each).
_OUTPUT_CONTRACTS: Mapping[str, type[Any]] = {
    "planner": PlannerOutput,
    "test_designer": __import__(
        "sfp_contracts.agents.test_designer", fromlist=["TestDesignerOutput"]
    ).TestDesignerOutput,
    "coder": CoderOutput,
    "reviewer": ReviewerOutput,
}


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
    extra: Mapping[str, str] = field(default_factory=dict)


def _abort(trace: list[str], pr_number: int | None, error: str) -> PipelineResult:
    """Build a fail-fast :class:`PipelineResult` carrying the partial trace."""
    return PipelineResult(success=False, pr_number=pr_number, error=error, trace=tuple(trace))


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

    Returns:
        The :class:`PipelineResult`. On success ``success=True``, ``pr_number`` is
        set, and the trace runs the full pipeline through merge + Done.
    """
    trace: list[str] = []

    # 1. Fetch the issue + parse its ADF description into a ParsedTicket.
    trace.append("jira.fetch_issue")
    issue = jira.fetch_issue(ticket_key)
    ticket = issue.parsed

    # Minimal/empty resolved context for this slice (PRSpec risk: the ticket
    # allows a minimal ResolvedContext; resolve_context over an empty
    # declaration yields exactly that — empty resolved + missing lists).
    declaration = TicketContextDeclaration()
    resolved: ResolvedContext = resolve_context(declaration, {}, ticket_id=ticket_key)

    # 2. Readiness gate (reuse the planner runtime per RESOLUTION 6).
    trace.append("evaluate_readiness")
    readiness = evaluate_readiness(
        ticket,
        resolved,
        runtime=runtimes["planner"],
        prompt_provider=prompt_provider,
        ticket_id=ticket_key,
    )
    if readiness.verdict is not ReadinessVerdict.READY:
        return _abort(trace, None, f"readiness gate not READY: {readiness.verdict.value}")

    # 3. Plan — decompose the ticket into one or more PR-specs.
    trace.append("plan")
    planner_output = plan(
        ticket,
        resolved,
        runtime=runtimes["planner"],
        prompt_provider=prompt_provider,
        ticket_id=ticket_key,
    )
    # RESOLUTION 5 — single-PR slice: process pr_specs[0] only. More than one
    # spec is a deterministic error (no fan-out in this slice).
    if len(planner_output.pr_specs) != 1:
        return _abort(
            trace,
            None,
            f"linear slice expects exactly 1 PR-spec, got {len(planner_output.pr_specs)}",
        )
    pr_spec: PrSpec = planner_output.pr_specs[0]

    # 4. Design the test plan (drives the Coder's test writing downstream).
    trace.append("design_tests")
    design_tests(
        ticket,
        resolved,
        runtime=runtimes["test_designer"],
        prompt_provider=prompt_provider,
        ticket_id=ticket_key,
    )

    # 5. Clone the repo (token-free origin written by RepoManager.clone).
    trace.append("repo_manager.clone")
    repo_manager.clone(repo_url, clone_dest)

    # 6. Materialise an isolated per-job worktree off the clone.
    trace.append("worktree.add")
    worktree = worktree_manager.add(job_id or ticket_key, base_branch, worktree_base)
    worktree_path = worktree.path

    # 7. Create + check out the PR branch in the worktree.
    trace.append("branch_manager.create_branch")
    branch_manager.create_branch(branch_name, ref=base_branch)
    trace.append("branch_manager.checkout")
    branch_manager.checkout(branch_name)

    # 8. Coder implements the PR-spec. RESOLUTION 3: the Coder runtime's cwd is
    #    set at LOOP time — the worktree path only exists after step 6. build()
    #    constructed the runtime with cwd=None; rebind it here. The runtime
    #    reads its `_cwd` when .run() builds options, so the rebind takes effect
    #    for this run. (Within the composition-root package boundary.)
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

    # 9-11. Local Execution Engine gates — build, tests, lint. Each is fail-fast:
    # a non-success aborts BEFORE push (no PR is opened against a red tree).
    trace.append("build")
    build_result = _run_build(worktree_path, runner=exec_runners["build"])
    if not build_result.success:
        return _abort(trace, None, f"build failed: exit_code={build_result.exit_code}")

    trace.append("run_tests")
    test_result = _run_tests(worktree_path, runner=exec_runners["test"])
    if not test_result.success:
        return _abort(trace, None, f"tests failed: exit_code={test_result.exit_code}")

    trace.append("lint")
    lint_result = _run_lint(worktree_path, runner=exec_runners["lint"])
    if not lint_result.success:
        return _abort(trace, None, f"lint failed: {lint_result}")

    # 12. Push the branch — RESOLUTION 4: object upload via RepoManager.push,
    #     NEVER GitProviderAdapter.push_branch (the Git Data refs API cannot
    #     upload locally-committed objects). Token is in-memory only.
    trace.append("repo_manager.push")
    repo_manager.push(worktree_path, branch_name)

    # 13. Open the pull request via the CODER adapter (sfp-coder-bot identity).
    trace.append("coder_adapter.create_pr")
    title = pr_title if pr_title is not None else f"{ticket_key}: {pr_spec.title}"
    body = (
        pr_body
        if pr_body is not None
        else f"JIRA: https://arconta.atlassian.net/browse/{ticket_key}"
    )
    pr = coder_adapter.create_pr(
        owner,
        repo_name,
        title=title,
        head=branch_name,
        base=base_branch,
        body=body,
    )

    # 14. Reviewer judges the PR.
    trace.append("review")
    review_output: ReviewerOutput = review(
        pr_spec,
        coder_output,
        resolved,
        runtime=runtimes["reviewer"],
        prompt_provider=prompt_provider,
        ticket_id=ticket_key,
    )

    # RESOLUTION 1: branch on review_status (no `event` field on ReviewerOutput).
    # Map the verdict to the GitHub `event` string for submit_review, then merge
    # + transition Done ONLY on APPROVED.
    event = "APPROVE" if review_output.review_status is ReviewStatus.APPROVED else "REQUEST_CHANGES"
    trace.append("reviewer_adapter.submit_review")
    reviewer_adapter.submit_review(
        owner,
        repo_name,
        pr.number,
        event=event,
        body=review_output.review_status.value,
    )

    if review_output.review_status is not ReviewStatus.APPROVED:
        # Review did not approve — fail fast: NO merge, NO Done transition.
        return _abort(trace, pr.number, f"review not approved: {review_output.review_status.value}")

    # 15. Merge (squash) via the CODER adapter + transition the ticket to Done.
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
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint: ``python -m workspace_worker.entrypoints.ticket_pipeline --ticket SFP-XXX``.

    Parses ``--ticket`` (required), an optional ``--slug`` (branch slug), and
    env-derived coordinates; calls :func:`build` then :func:`run_pipeline`, prints
    a one-line outcome, and returns a process exit code (``0`` on success).
    """
    parser = argparse.ArgumentParser(
        prog="workspace_worker.entrypoints.ticket_pipeline",
        description="Run one ticket through the SFP vertical slice.",
    )
    parser.add_argument("--ticket", required=True, help="Jira issue key, e.g. SFP-224")
    parser.add_argument("--slug", default="slice", help="Short branch slug (default: slice)")
    parser.add_argument("--base-branch", default="main", help="PR base branch")
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
