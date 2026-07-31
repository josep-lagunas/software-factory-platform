"""Tests for :mod:`workspace_worker.entrypoints.slice` — the vertical slice (SFP-224).

The loop (:func:`run_ticket`) is PURE — every collaborator is injected, so these
tests drive it with fakes:

* :class:`FakeJiraClient` — canned :class:`ParsedTicket` + recorded transitions.
* :class:`FakeRuntime` — one per role, returning canned contract outputs keyed
  by ``request.agent``; records the cwd it sees when ``.run()`` is invoked
  (the AP-CWD assertion).
* :class:`FakeRepoManager` — records clone + push; push is the object-upload
  path (RESOLUTION 4).
* :class:`FakeGitAdapter` — records create_pr / submit_review / merge_pr and
  RAISES if ``push_branch`` is ever called (RESOLUTION 4 negative pin).
* stub exec runners returning canned :class:`CompletedProcess` results.
* :class:`FakeWorktreeManager` / :class:`FakeBranchManager` — deterministic
  worktree path + recorded branch ops.

The :func:`build` composition-root tests monkeypatch the real constructors with
recording fakes (no real SDK / HTTP / env parsing for the typed settings).
"""

from __future__ import annotations

import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from sfp_agent_runtime.interfaces import AgentRunRequest, AgentRunResult
from sfp_config import LocalSecretProvider, SecretRef
from sfp_contracts.agents.readiness import ParsedTicket
from workspace_worker.entrypoints import slice as slice_mod
from workspace_worker.entrypoints.slice import SliceDeps, SliceResult, build, run_ticket
from workspace_worker.infrastructure.settings import WorkspaceWorkerSettings
from workspace_worker.repo.branch import BranchResult
from workspace_worker.repo.git.adapter import (
    GitMergeResult,
    PullRequestResult,
    ReviewResult,
)
from workspace_worker.repo.manager import CloneResult, PushResult
from workspace_worker.repo.worktree import WorktreeResult

TICKET = "SFP-224"
OWNER = "arconta"
REPO = "sfp"
REPO_URL = f"https://github.com/{OWNER}/{REPO}.git"
BRANCH = "sfp-224-slice"
PR_NUMBER = 42


# --------------------------------------------------------------------------- #
# Canned contract outputs (parsed-JSON dicts the fakes return).
# --------------------------------------------------------------------------- #


def _ready_ticket() -> ParsedTicket:
    """A ParsedTicket with all eight ID-070 sections present (rubric passes)."""
    return ParsedTicket(
        context="ctx",
        requirements="req",
        files_to_create_modify="files",
        implementation_notes="notes",
        references="refs",
        context_outputs_required_inputs="io",
        acceptance_criteria="ac",
        dependencies="dep",
    )


def _readiness_ready() -> dict[str, Any]:
    return {
        "ticket_id": TICKET,
        "verdict": "READY",
        "blocking_ambiguities": [],
        "missing_inputs": [],
        "rubric_results": {
            "context": True,
            "requirements": True,
            "files_to_create_modify": True,
            "implementation_notes": True,
            "references": True,
            "context_outputs_required_inputs": True,
            "acceptance_criteria": True,
            "dependencies": True,
        },
    }


def _readiness_not_ready() -> dict[str, Any]:
    return {
        "ticket_id": TICKET,
        "verdict": "NEEDS_CLARIFICATION",
        "blocking_ambiguities": ["semantic gap"],
        "missing_inputs": [],
        "rubric_results": {
            "context": True,
            "requirements": True,
            "files_to_create_modify": True,
            "implementation_notes": True,
            "references": True,
            "context_outputs_required_inputs": True,
            "acceptance_criteria": True,
            "dependencies": True,
        },
    }


def _planner_output(n_specs: int = 1) -> dict[str, Any]:
    spec = {
        "id": "PRSPEC-SFP-224",
        "title": "Composition root",
        "goal": "Wire the slice.",
        "scope": ["slice.py"],
        "out_of_scope": [],
        "acceptance_criteria": ["Loop runs end to end."],
        "dependencies": [],
        "validation_profile": "LEVEL_1_INTERNAL",
        "validation_profile_reason": "internal infra",
        "required_gates": ["build", "test", "lint"],
        "likely_files_or_modules": ["slice.py"],
        "risks": [],
        "implementation_notes": "n",
    }
    return {"pr_specs": [spec for _ in range(n_specs)]}


def _test_designer_output() -> dict[str, Any]:
    return {
        "pr_spec_id": "PRSPEC-SFP-224",
        "test_plan": {
            "unit_tests": ["happy path"],
            "integration_tests": [],
            "e2e_or_smoke_tests": [],
            "negative_tests": [],
            "edge_cases": [],
            "regression_risks": [],
            "required_validation_commands": ["uv run pytest"],
        },
    }


def _coder_output() -> dict[str, Any]:
    return {
        "pr_spec_id": "PRSPEC-SFP-224",
        "branch_name": BRANCH,
        "pull_request_url": f"https://github.com/{OWNER}/{REPO}/pull/{PR_NUMBER}",
        "files_changed": ["slice.py"],
        "tests_added_or_updated": ["test_slice.py"],
        "validation_status": "PASSED",
        "validation_evidence": ["build green"],
        "known_limitations": [],
    }


def _reviewer_output(approved: bool) -> dict[str, Any]:
    return {
        "pr_spec_id": "PRSPEC-SFP-224",
        "review_status": "APPROVED" if approved else "CHANGES_REQUESTED",
        "quality_gates": {
            "blueprint_compliance": approved,
            "acceptance_criteria_satisfied": approved,
            "test_plan_satisfied": approved,
            "no_unrelated_changes": True,
            "maintainability_acceptable": approved,
            "security_acceptable": True,
        },
    }


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #


class FakeJiraClient:
    def __init__(self, parsed: ParsedTicket | None = None) -> None:
        self.parsed = parsed if parsed is not None else _ready_ticket()
        self.transitions: list[tuple[str, str]] = []

    def fetch_issue(self, key: str) -> Any:
        class _Issue:
            def __init__(self, k: str, p: ParsedTicket) -> None:
                self.key = k
                self.parsed = p

        return _Issue(key, self.parsed)

    def transition(self, key: str, transition_id: str) -> Any:
        self.transitions.append((key, transition_id))
        return None


class FakeRuntime:
    """Per-role runtime: returns a canned output keyed by ``request.agent``.

    Exposes ``_cwd`` (the attribute the loop rebinds at code time, RESOLUTION 3)
    and records the value it sees when ``.run()`` is invoked.
    """

    def __init__(self, outputs: Mapping[str, dict[str, Any] | None]) -> None:
        self._outputs = outputs
        self._cwd: str | None = None
        self.calls: list[dict[str, Any]] = []

    def run(self, request: AgentRunRequest) -> AgentRunResult:
        self.calls.append(
            {
                "agent": request.agent,
                "ticket_id": request.ticket_id,
                "cwd": self._cwd,
            }
        )
        out = self._outputs.get(request.agent)
        if out is None:
            return AgentRunResult(
                agent=request.agent,
                ticket_id=request.ticket_id,
                success=False,
                error=f"no canned output for agent={request.agent!r}",
            )
        return AgentRunResult(
            agent=request.agent, ticket_id=request.ticket_id, success=True, output=out
        )


def _make_runtimes(
    *,
    readiness: dict[str, Any] | None = None,
    planner_specs: int = 1,
    approved: bool = True,
) -> tuple[dict[str, FakeRuntime], dict[str, FakeRuntime]]:
    """Build the 4 per-role runtimes + return (runtimes_dict, handles).

    The planner runtime serves BOTH the readiness (agent="readiness") and plan
    (agent="planner") requests (RESOLUTION 6: readiness reuses the planner
    runtime). Other roles serve only their own agent name.
    """
    planner = FakeRuntime(
        {"readiness": readiness or _readiness_ready(), "planner": _planner_output(planner_specs)}
    )
    test_designer = FakeRuntime({"test_designer": _test_designer_output()})
    coder = FakeRuntime({"coder": _coder_output()})
    reviewer = FakeRuntime({"reviewer": _reviewer_output(approved)})
    runtimes = {
        "planner": planner,
        "test_designer": test_designer,
        "coder": coder,
        "reviewer": reviewer,
    }
    return runtimes, {
        "planner": planner,
        "test_designer": test_designer,
        "coder": coder,
        "reviewer": reviewer,
    }


class FakeRepoManager:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def clone(self, repo_url: str, dest: Path) -> CloneResult:
        self.calls.append(("clone", repo_url, str(dest)))
        return CloneResult(path=dest, cloned=True)

    def push(
        self, repo_path: str | Path, branch: str, *, remote_url: str | None = None
    ) -> PushResult:
        self.calls.append(("push", str(repo_path), branch))
        return PushResult(path=Path(repo_path), branch=branch, pushed=True)


class FakeGitAdapter:
    """Records create_pr/submit_review/merge_pr; push_branch RAISES (RESOLUTION 4)."""

    def __init__(self, pr_number: int = PR_NUMBER) -> None:
        self.pr_number = pr_number
        self.calls: list[tuple[str, ...]] = []

    def create_pr(
        self, owner: str, repo: str, *, title: str, head: str, base: str, body: str
    ) -> PullRequestResult:
        self.calls.append(("create_pr", owner, repo, title, head, base, body))
        return PullRequestResult(
            owner=owner,
            repo=repo,
            number=self.pr_number,
            url=f"https://github.com/{owner}/{repo}/pull/{self.pr_number}",
            state="open",
        )

    def submit_review(
        self, owner: str, repo: str, number: int, *, event: str, body: str
    ) -> ReviewResult:
        self.calls.append(("submit_review", owner, repo, number, event, body))
        return ReviewResult(owner=owner, repo=repo, number=number, review_id=1, state=event)

    def merge_pr(
        self, owner: str, repo: str, number: int, *, merge_method: str = "squash"
    ) -> GitMergeResult:
        self.calls.append(("merge_pr", owner, repo, number, merge_method))
        return GitMergeResult(
            owner=owner,
            repo=repo,
            pull_number=number,
            sha="deadbeef",
            merged=True,
            merge_method=merge_method,
        )

    def push_branch(self, *args: Any, **kwargs: Any) -> None:
        raise AssertionError(
            "GitProviderAdapter.push_branch must NOT be called: object upload is "
            "RepoManager.push (RESOLUTION 4)"
        )


class FakeWorktreeManager:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def add(self, job_id: str, ref: str, base_dir: Path) -> WorktreeResult:
        path = base_dir / job_id
        path.mkdir(parents=True, exist_ok=True)
        self.calls.append(("add", job_id, ref, str(base_dir)))
        return WorktreeResult(path=path, job_id=job_id)

    def remove(self, *args: Any, **kwargs: Any) -> None:
        return None


class FakeBranchManager:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def create_branch(self, name: str, *, ref: str = "HEAD") -> BranchResult:
        self.calls.append(("create_branch", name, ref))
        return BranchResult(name=name, ref=ref)

    def checkout(self, name: str) -> None:
        self.calls.append(("checkout", name))

    def delete(self, *args: Any, **kwargs: Any) -> None:
        return None


class FakePromptProvider:
    def get_prompt(self, agent: str, task: str) -> str:
        return f"prompt:{agent}/{task}"


def _ok_runner(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")


def _fail_runner(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr="boom")


def _exec_runners(
    *, build_ok: bool = True, test_ok: bool = True, lint_ok: bool = True
) -> dict[str, Any]:
    return {
        "build": _ok_runner if build_ok else _fail_runner,
        "test": _ok_runner if test_ok else _fail_runner,
        "lint": _ok_runner if lint_ok else _fail_runner,
    }


def _run(
    tmp_path: Path,
    runtimes: Mapping[str, FakeRuntime],
    *,
    jira: FakeJiraClient | None = None,
    repo_manager: FakeRepoManager | None = None,
    coder_adapter: FakeGitAdapter | None = None,
    reviewer_adapter: FakeGitAdapter | None = None,
    exec_runners: Mapping[str, Any] | None = None,
) -> tuple[SliceResult, dict[str, Any]]:
    """Wire every fake and call run_ticket; return (result, fakes-dict)."""
    jira = jira or FakeJiraClient()
    repo_manager = repo_manager or FakeRepoManager()
    coder_adapter = coder_adapter or FakeGitAdapter()
    reviewer_adapter = reviewer_adapter or FakeGitAdapter()
    exec_runners = exec_runners or _exec_runners()
    worktree_manager = FakeWorktreeManager()
    branch_manager = FakeBranchManager()
    clone_dest = tmp_path / "clone"
    worktree_base = tmp_path / "wt"

    result = run_ticket(
        TICKET,
        jira=jira,
        repo_manager=repo_manager,
        coder_adapter=coder_adapter,
        reviewer_adapter=reviewer_adapter,
        runtimes=runtimes,
        exec_runners=exec_runners,
        prompt_provider=FakePromptProvider(),
        worktree_manager=worktree_manager,
        branch_manager=branch_manager,
        owner=OWNER,
        repo_name=REPO,
        repo_url=REPO_URL,
        clone_dest=clone_dest,
        worktree_base=worktree_base,
        branch_name=BRANCH,
    )
    fakes = {
        "jira": jira,
        "repo_manager": repo_manager,
        "coder_adapter": coder_adapter,
        "reviewer_adapter": reviewer_adapter,
        "worktree_manager": worktree_manager,
        "branch_manager": branch_manager,
    }
    return result, fakes


# --------------------------------------------------------------------------- #
# Happy path
# --------------------------------------------------------------------------- #


def test_happy_path_runs_full_pipeline_and_transitions_done(tmp_path: Path) -> None:
    runtimes, handles = _make_runtimes(approved=True)

    result, fakes = _run(tmp_path, runtimes)

    assert result.success is True
    assert result.pr_number == PR_NUMBER
    assert result.error is None
    # Jira was transitioned to Done (id 51) exactly once.
    assert fakes["jira"].transitions == [(TICKET, "51")]
    # The PR was created on the coder adapter, merged via squash.
    assert any(c[0] == "create_pr" for c in fakes["coder_adapter"].calls)
    assert ("merge_pr", OWNER, REPO, PR_NUMBER, "squash") in fakes["coder_adapter"].calls
    # The reviewer adapter submitted APPROVE (not the coder adapter).
    assert ("submit_review", OWNER, REPO, PR_NUMBER, "APPROVE", "APPROVED") in fakes[
        "reviewer_adapter"
    ].calls
    # Object upload went through RepoManager.push (never the adapter).
    assert any(c[0] == "push" for c in fakes["repo_manager"].calls)
    assert all(
        "push_branch" not in c[0] for c in fakes["coder_adapter"].calls
    )  # negative pin via the raising fake


def test_happy_path_trace_is_the_exact_linear_order(tmp_path: Path) -> None:
    runtimes, _ = _make_runtimes(approved=True)
    result, _ = _run(tmp_path, runtimes)

    expected = [
        "jira.fetch_issue",
        "evaluate_readiness",
        "plan",
        "design_tests",
        "repo_manager.clone",
        "worktree.add",
        "branch_manager.create_branch",
        "branch_manager.checkout",
        "code",
        "build",
        "run_tests",
        "lint",
        "repo_manager.push",
        "coder_adapter.create_pr",
        "review",
        "reviewer_adapter.submit_review",
        "coder_adapter.merge_pr",
        "jira.transition",
    ]
    assert list(result.trace) == expected


def test_coder_runtime_cwd_is_set_to_worktree_path_at_loop_time(tmp_path: Path) -> None:
    """AP-CWD (RESOLUTION 3): the coder runtime sees cwd=worktree_path at run()."""
    runtimes, handles = _make_runtimes(approved=True)
    _run(tmp_path, runtimes)

    coder_calls = handles["coder"].calls
    assert len(coder_calls) == 1
    expected_worktree = tmp_path / "wt" / TICKET
    assert coder_calls[0]["cwd"] == str(expected_worktree)


def test_push_branch_is_never_called_object_upload_uses_repo_manager(
    tmp_path: Path,
) -> None:
    """RESOLUTION 4 negative pin: FakeGitAdapter.push_branch raises if invoked."""
    runtimes, _ = _make_runtimes(approved=True)
    # If the loop called push_branch, the FakeGitAdapter would raise AssertionError
    # and the test would error out rather than return success.
    result, _ = _run(tmp_path, runtimes)
    assert result.success is True


# --------------------------------------------------------------------------- #
# Fail-fast branches
# --------------------------------------------------------------------------- #


def test_readiness_not_ready_aborts_before_plan(tmp_path: Path) -> None:
    runtimes, handles = _make_runtimes(readiness=_readiness_not_ready(), approved=True)
    result, fakes = _run(tmp_path, runtimes)

    assert result.success is False
    assert result.pr_number is None
    assert "readiness" in (result.error or "")
    assert list(result.trace) == ["jira.fetch_issue", "evaluate_readiness"]
    # Nothing downstream ran.
    assert fakes["repo_manager"].calls == []
    assert fakes["coder_adapter"].calls == []
    assert fakes["jira"].transitions == []
    # The planner runtime served readiness but plan never ran (only 1 call).
    assert len(handles["planner"].calls) == 1
    assert handles["planner"].calls[0]["agent"] == "readiness"


def test_build_failure_aborts_before_push_and_pr(tmp_path: Path) -> None:
    runtimes, _ = _make_runtimes(approved=True)
    exec_runners = _exec_runners(build_ok=False)
    result, fakes = _run(tmp_path, runtimes, exec_runners=exec_runners)

    assert result.success is False
    assert "build failed" in (result.error or "")
    # The trace stops right after 'build' — no push, no PR.
    assert result.trace[-1] == "build"
    assert all(c[0] != "push" for c in fakes["repo_manager"].calls)
    assert fakes["coder_adapter"].calls == []
    assert fakes["jira"].transitions == []


def test_test_failure_aborts_before_push(tmp_path: Path) -> None:
    runtimes, _ = _make_runtimes(approved=True)
    exec_runners = _exec_runners(test_ok=False)
    result, fakes = _run(tmp_path, runtimes, exec_runners=exec_runners)

    assert result.success is False
    assert "tests failed" in (result.error or "")
    assert result.trace[-1] == "run_tests"
    assert all(c[0] != "push" for c in fakes["repo_manager"].calls)


def test_lint_failure_aborts_before_push(tmp_path: Path) -> None:
    runtimes, _ = _make_runtimes(approved=True)
    exec_runners = _exec_runners(lint_ok=False)
    result, fakes = _run(tmp_path, runtimes, exec_runners=exec_runners)

    assert result.success is False
    assert "lint failed" in (result.error or "")
    assert result.trace[-1] == "lint"
    assert all(c[0] != "push" for c in fakes["repo_manager"].calls)


def test_review_request_changes_does_not_merge_and_does_not_transition(
    tmp_path: Path,
) -> None:
    runtimes, _ = _make_runtimes(approved=False)
    result, fakes = _run(tmp_path, runtimes)

    # Not approved: a PR was opened but NOT merged; no Done transition.
    assert result.success is False
    assert result.pr_number == PR_NUMBER
    assert "review not approved" in (result.error or "")
    # Reviewer submitted REQUEST_CHANGES.
    assert (
        "submit_review",
        OWNER,
        REPO,
        PR_NUMBER,
        "REQUEST_CHANGES",
        "CHANGES_REQUESTED",
    ) in fakes["reviewer_adapter"].calls
    # No merge, no Done transition.
    assert all(c[0] != "merge_pr" for c in fakes["coder_adapter"].calls)
    assert fakes["jira"].transitions == []
    # Trace stops after submit_review.
    assert result.trace[-1] == "reviewer_adapter.submit_review"


def test_more_than_one_pr_spec_is_a_deterministic_error(tmp_path: Path) -> None:
    """RESOLUTION 5: linear slice processes pr_specs[0] only; >1 is an error."""
    runtimes, _ = _make_runtimes(planner_specs=2, approved=True)
    result, fakes = _run(tmp_path, runtimes)

    assert result.success is False
    assert "exactly 1 PR-spec" in (result.error or "")
    assert result.trace[-1] == "plan"
    # Nothing downstream of plan ran.
    assert fakes["repo_manager"].calls == []
    assert fakes["jira"].transitions == []


# --------------------------------------------------------------------------- #
# Composition root — build() constructs the real collaborators (monkeypatched)
# --------------------------------------------------------------------------- #


def test_build_constructs_dual_adapters_and_four_runtimes_from_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """build() wires: 4 ClaudeAgentRuntime (coder cwd=None), 2 GitProviderAdapter
    (coder+reviewer tokens), 1 RepoManager (coder token), 1 JiraClient."""
    # Tokens resolved from env by LocalSecretProvider.
    monkeypatch.setenv("GITHUB_TOKEN_CODER", "coder-tok")
    monkeypatch.setenv("GITHUB_TOKEN_REVIEWER", "reviewer-tok")
    monkeypatch.setenv("JIRA_API_TOKEN", "jira-tok")
    monkeypatch.setenv("SFP_JIRA_EMAIL", "bot@example.com")

    settings = WorkspaceWorkerSettings(
        anthropic_base_url="https://llm.example.com",
        default_model="glm-x",
        llm_provider_secret_ref=SecretRef(name="LLM_TOKEN"),
    )
    sp = LocalSecretProvider(secrets_file=None)

    # Recording fakes for every real constructor build() references.
    runtime_instances: list[dict[str, Any]] = []

    def fake_runtime_ctor(
        settings_, sp_, contract, *, max_turns=None, cwd=None, model_resolver=None, **kw
    ):
        inst = {"contract": contract, "max_turns": max_turns, "cwd": cwd}
        runtime_instances.append(inst)
        return inst

    jira_calls: list[tuple[Any, ...]] = []
    repo_calls: list[Any] = []
    adapter_calls: list[tuple[Any, ...]] = []

    def fake_jira_ctor(site, email, token, **kw):
        jira_calls.append((site, email, token))
        return object()

    def fake_repo_ctor(token, **kw):
        repo_calls.append(token)
        return object()

    def fake_adapter_ctor(token, **kw):
        adapter_calls.append(token)
        return object()

    def fake_wt_ctor(repo_path, **kw):
        return object()

    def fake_branch_ctor(repo_path, **kw):
        return object()

    def fake_prompt_ctor(path, **kw):
        return object()

    monkeypatch.setattr(slice_mod, "ClaudeAgentRuntime", fake_runtime_ctor)
    monkeypatch.setattr(slice_mod, "JiraClient", fake_jira_ctor)
    monkeypatch.setattr(slice_mod, "RepoManager", fake_repo_ctor)
    monkeypatch.setattr(slice_mod, "GitProviderAdapter", fake_adapter_ctor)
    monkeypatch.setattr(slice_mod, "WorktreeManager", fake_wt_ctor)
    monkeypatch.setattr(slice_mod, "BranchManager", fake_branch_ctor)
    monkeypatch.setattr(slice_mod, "PromptBuilder", fake_prompt_ctor)

    deps = build(
        TICKET,
        slug="slice",
        owner=OWNER,
        repo_name=REPO,
        worktree_base=tmp_path,
        settings=settings,
        secret_provider=sp,
    )

    assert isinstance(deps, SliceDeps)
    # Four runtimes, one per role; the coder was built with cwd=None (RESOLUTION 3).
    assert len(runtime_instances) == 4
    coder_inst = next(r for r in runtime_instances if r["contract"].__name__ == "CoderOutput")
    assert coder_inst["cwd"] is None
    # Per-role max_turns (RESOLUTION 6).
    turns_by_contract = {r["contract"].__name__: r["max_turns"] for r in runtime_instances}
    assert turns_by_contract["PlannerOutput"] == 5
    assert turns_by_contract["TestDesignerOutput"] == 5
    assert turns_by_contract["CoderOutput"] == 50
    assert turns_by_contract["ReviewerOutput"] == 5
    # Dual adapters: coder token + reviewer token (RESOLUTION 2 / ID-073).
    assert adapter_calls == ["coder-tok", "reviewer-tok"]
    # RepoManager uses the CODER token (object upload is a Coder-side op, ID-035).
    assert repo_calls == ["coder-tok"]
    # JiraClient got the site/email/token.
    assert jira_calls == [("https://arconta.atlassian.net", "bot@example.com", "jira-tok")]
    # Branch name follows the sfp-<key>-<slug> convention.
    assert deps.branch_name == "sfp-sfp-224-slice"
    assert deps.repo_url == REPO_URL
    assert deps.done_transition_id == "51"


def test_main_returns_zero_on_success_and_nonzero_on_abort(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """main() parses --ticket, calls build() + run_ticket, returns an exit code."""
    # Stub build() to return SliceDeps-shaped fakes so no real I/O occurs.
    runtimes, _ = _make_runtimes(approved=True)

    fake_deps = SliceDeps(
        jira=FakeJiraClient(),
        repo_manager=FakeRepoManager(),
        coder_adapter=FakeGitAdapter(),
        reviewer_adapter=FakeGitAdapter(),
        runtimes=runtimes,
        exec_runners=_exec_runners(),
        prompt_provider=FakePromptProvider(),
        worktree_manager=FakeWorktreeManager(),
        branch_manager=FakeBranchManager(),
        owner=OWNER,
        repo_name=REPO,
        repo_url=REPO_URL,
        clone_dest=tmp_path / "clone",
        worktree_base=tmp_path,
        branch_name=BRANCH,
    )
    monkeypatch.setattr(slice_mod, "build", lambda *a, **kw: fake_deps)

    rc = slice_mod.main(["--ticket", TICKET, "--slug", "slice"])
    assert rc == 0

    # Abort path: readiness not ready -> rc == 1.
    runtimes_abort, _ = _make_runtimes(readiness=_readiness_not_ready(), approved=True)
    # Rebuild a fresh deps with the aborting runtimes (dataclass is frozen).
    fake_deps_abort = SliceDeps(
        jira=FakeJiraClient(),
        repo_manager=FakeRepoManager(),
        coder_adapter=FakeGitAdapter(),
        reviewer_adapter=FakeGitAdapter(),
        runtimes=runtimes_abort,
        exec_runners=_exec_runners(),
        prompt_provider=FakePromptProvider(),
        worktree_manager=FakeWorktreeManager(),
        branch_manager=FakeBranchManager(),
        owner=OWNER,
        repo_name=REPO,
        repo_url=REPO_URL,
        clone_dest=tmp_path / "clone",
        worktree_base=tmp_path,
        branch_name=BRANCH,
    )
    monkeypatch.setattr(slice_mod, "build", lambda *a, **kw: fake_deps_abort)
    rc_abort = slice_mod.main(["--ticket", TICKET])
    assert rc_abort == 1
