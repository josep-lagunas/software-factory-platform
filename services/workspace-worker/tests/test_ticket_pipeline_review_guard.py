"""Guard tests for the reviewer-malfunction seam (SFP-249 → SFP-252).

The REVIEWER_MALFUNCTION guard (:func:`is_malformed_rationale` +
:func:`_guarded_review` in :mod:`workspace_worker.entrypoints.ticket_pipeline`)
protects BOTH review sites (the primary review and the SFP-241 pre-merge
re-review) against a reviewer verdict that carries no reviewer-authored
prose:

* SFP-249 — the EMPTY leg: a rationale that is ``None``/whitespace-only is a
  malfunction on EVERY status (including ``APPROVED``).
* SFP-252 — the widened RENDERING-ONLY leg: a rationale that is EXACTLY the
  runtime's deterministic rendering of the structured verdict
  (:func:`workspace_worker.agent_runtime.runtime.render_structured_verdict`)
  carries no reviewer prose either, so it is a malfunction on every status
  too. Gate values are NEVER the discriminator — prose presence is: a
  genuinely terrible PR can legitimately fail all six gates WITH prose.

Measured blind spot this closes (documentation only — the log is never parsed
at runtime): the SFP-122 first run of 2026-09-12 (PR #167, closed), recorded
in ``logs/run-SFP-122-20260911T233343Z.log`` — a verdict whose final_text was
only the runtime's structured rendering passed the empty-strip check and an
infrastructure malfunction masqueraded as a code rejection.

Recovery (unchanged from SFP-249): exactly one review re-run; a second
malformed verdict aborts with :data:`REVIEWER_MALFUNCTION_ERROR` (deliberately
distinct from the ``"review not approved: ..."`` code-verdict family) and
posts :data:`REVIEWER_MALFUNCTION_COMMENT` to the PR conversation.

The SFP-249 guard cases were relocated here from ``test_ticket_pipeline.py``
(kept in sync; the shared fakes/helpers are imported from that module — the
same sibling-import precedent as ``test_repo_branch`` → ``test_repo_manager``).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest
from sfp_agent_runtime.interfaces import AgentRunRequest
from sfp_config import LocalSecretProvider, SecretRef
from sfp_contracts.agents.reviewer import ReviewerOutput, ReviewStatus
from test_ticket_pipeline import (  # type: ignore[no-redef]
    _REUSED_PR_NUMBER,
    HEAD_SHA,
    OWNER,
    PR_NUMBER,
    REPO,
    FakeGitAdapter,
    FakeRuntime,
    _already_exists_error,
    _make_runtimes,
    _reused_pr,
    _review,
    _reviewer_output,
    _run,
    _StructuredOnlyMessage,
    _StructuredOnlyQueryFn,
)
from workspace_worker.agent_runtime.runtime import (
    ClaudeAgentRuntime,
    render_structured_verdict,
)
from workspace_worker.entrypoints.ticket_pipeline import (
    REVIEWER_MALFUNCTION_COMMENT,
    REVIEWER_MALFUNCTION_ERROR,
    _review_body,
    is_malformed_rationale,
)
from workspace_worker.infrastructure.settings import WorkspaceWorkerSettings

_RATIONALE = "Solid: matches the spec; gates all pass."

_GATE_KEYS = (
    "blueprint_compliance",
    "acceptance_criteria_satisfied",
    "test_plan_satisfied",
    "no_unrelated_changes",
    "maintainability_acceptable",
    "security_acceptable",
)


def _verdict(status: ReviewStatus, *, gates: bool = True) -> dict[str, Any]:
    """A reviewer-verdict dict for ANY status with all six gates set to
    ``gates`` (``gates=False`` is the genuinely-terrible-PR shape the
    all-false-gates cases need — ``_reviewer_output`` only covers
    APPROVED/CHANGES_REQUESTED)."""
    return {
        "pr_spec_id": "PRSPEC-SFP-224",
        "review_status": status.value,
        "quality_gates": {key: gates for key in _GATE_KEYS},
    }


def _rendering_of(verdict: Mapping[str, Any]) -> str:
    """The runtime's deterministic rendering of ``verdict`` — computed through
    the SAME production path the guard uses (validate → ``model_dump(mode=
    "json")`` → :func:`render_structured_verdict`), never re-derived with
    test-local JSON."""
    output = ReviewerOutput.model_validate(dict(verdict))
    return render_structured_verdict(output.model_dump(mode="json"))


class _StructuredWithTextQueryFn:
    """``query_fn`` yielding a final message with BOTH agent prose
    (``result``) and ``structured_output`` — the healthy output_format shape
    where the reviewer authored a textual rationale alongside its verdict."""

    def __init__(self, result: str, structured: Mapping[str, Any]) -> None:
        self._result = result
        self._structured = structured
        self.calls: list[dict[str, Any]] = []

    def __call__(self, *, prompt: str, options: Any) -> Any:
        self.calls.append({"prompt": prompt, "options": options})
        return self._stream()

    async def _stream(self):  # noqa: ANN202 - async gen
        yield _StructuredOnlyMessage(result=self._result, structured_output=dict(self._structured))


def _real_reviewer_runtime(monkeypatch: pytest.MonkeyPatch, query_fn: Any) -> ClaudeAgentRuntime:
    """The REAL reviewer runtime (same wiring as the composition root), fed by
    a fake ``query_fn`` — no CLI, no network (deterministic, MAS §12.7)."""
    monkeypatch.setenv("LLM_TOKEN", "test-token")
    return ClaudeAgentRuntime(
        WorkspaceWorkerSettings(
            anthropic_base_url="https://api.example.com",
            default_model="claude-sonnet-4",
            llm_provider_secret_ref=SecretRef(name="LLM_TOKEN"),
        ),
        LocalSecretProvider(secrets_file=None),
        ReviewerOutput,
        query_fn=query_fn,
        max_retries=0,
    )


# --------------------------------------------------------------------------- #
# Pure predicate — empty leg (SFP-249 regression)
# --------------------------------------------------------------------------- #


def test_is_malformed_rationale_rows_per_status_including_approved() -> None:
    """Row-test the pure validator: an EMPTY (post-strip) rationale is a
    REVIEWER_MALFUNCTION on EVERY status — including APPROVED. A non-empty
    rationale that is not the structured rendering is never malformed,
    regardless of status (the predicate never even receives the status)."""
    for status in (
        ReviewStatus.APPROVED,
        ReviewStatus.CHANGES_REQUESTED,
        ReviewStatus.BLOCKED,
        ReviewStatus.NEEDS_HUMAN_DECISION,
    ):
        rendering = _rendering_of(_verdict(status))
        # Malformed inputs: None (no final text), "", and whitespace-only —
        # malformed even though the rendering is supplied (empty leg first).
        assert is_malformed_rationale(None, rendering) is True, status
        assert is_malformed_rationale("", rendering) is True, status
        assert is_malformed_rationale("   \n\t ", rendering) is True, status
        # Valid inputs: any non-empty post-strip text that is not the
        # rendering.
        assert is_malformed_rationale(_RATIONALE, rendering) is False, status
        assert is_malformed_rationale(f"  {_RATIONALE} \n", rendering) is False, status
        # Belt-and-braces: with no rendering supplied (None/empty), only the
        # empty leg applies — prose stays valid.
        assert is_malformed_rationale(_RATIONALE, None) is False, status
        assert is_malformed_rationale(_RATIONALE, "") is False, status


# --------------------------------------------------------------------------- #
# Pure predicate — rendering-only leg (SFP-252)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "status", [ReviewStatus.APPROVED, ReviewStatus.BLOCKED], ids=lambda s: s.value
)
def test_rendering_equal_rationale_is_malfunction(status: ReviewStatus) -> None:
    """SFP-252 AC: a rationale EXACTLY equal to the runtime's deterministic
    rendering of the verdict is a malfunction — parametrized over APPROVED
    and BLOCKED alike (an all-gates-true APPROVE whose only text is the
    rendering is exactly as untrustworthy as the BLOCKED twin)."""
    rendering = _rendering_of(_verdict(status))
    assert is_malformed_rationale(rendering, rendering) is True
    # Whitespace-padded rendering is still prose-free text.
    assert is_malformed_rationale(f"  {rendering} \n", rendering) is True
    # The equality is against the rendering of the SAME verdict: the
    # rendering of a DIFFERENT verdict (BLOCKED with all-false gates) does
    # not match — only the verdict's own rendering classifies.
    other = _rendering_of(_verdict(ReviewStatus.BLOCKED, gates=False))
    assert other != rendering
    assert is_malformed_rationale(other, rendering) is False


def test_prose_rationale_with_all_false_gates_is_not_malfunction() -> None:
    """SFP-252 AC: prose presence — never gate values — is the discriminator.
    A genuinely terrible PR (ALL six gates false) rejected WITH reviewer
    prose is NOT a malfunction, even though its structured rendering exists
    and could have been the whole final_text."""
    verdict = _verdict(ReviewStatus.BLOCKED, gates=False)
    rendering = _rendering_of(verdict)
    prose = "All six gates fail: blueprint drift, unmet acceptance criteria, missing tests."
    assert is_malformed_rationale(prose, rendering) is False
    # … while the rendering-only twin of the SAME verdict IS a malfunction.
    assert is_malformed_rationale(rendering, rendering) is True


# --------------------------------------------------------------------------- #
# Review body composition (SFP-249, relocated)
# --------------------------------------------------------------------------- #


def test_review_body_carries_status_word_plus_rationale() -> None:
    """AC: the submitted body is the status-word prefix followed by the
    reviewer's final text; the BARE status word only when the text is
    empty/None (which the guard prevents from ever being submitted)."""
    assert _review_body(ReviewStatus.APPROVED, "Looks good.") == "APPROVED: Looks good."
    assert (
        _review_body(ReviewStatus.CHANGES_REQUESTED, "  Needs tests for the abort path.  ")
        == "CHANGES_REQUESTED: Needs tests for the abort path."
    )
    assert _review_body(ReviewStatus.BLOCKED, None) == "BLOCKED"
    assert _review_body(ReviewStatus.APPROVED, "") == "APPROVED"
    assert _review_body(ReviewStatus.APPROVED, "   ") == "APPROVED"


def test_pipeline_submits_review_body_with_status_and_reviewer_text(
    tmp_path: Any,
) -> None:
    """AC: the GitHub review body submitted by the pipeline carries the status
    word PLUS the reviewer's final text — never the bare word when a rationale
    exists (pinned end-to-end through run_pipeline)."""
    runtimes, _ = _make_runtimes(
        approved=True, reviewer_final_texts=["Matches the PRSpec end to end."]
    )
    result, fakes = _run(tmp_path, runtimes)

    assert result.success is True
    assert (
        "submit_review",
        OWNER,
        REPO,
        PR_NUMBER,
        "APPROVE",
        "APPROVED: Matches the PRSpec end to end.",
    ) in fakes["reviewer_adapter"].calls


# --------------------------------------------------------------------------- #
# Guard flow end-to-end — empty leg (SFP-249, relocated)
# --------------------------------------------------------------------------- #


def test_malfunction_then_valid_retry_proceeds_to_merge(tmp_path: Any) -> None:
    """AC: a malformed first verdict triggers EXACTLY one retry; the retry's
    valid APPROVED verdict is acted on normally (merge + Done). No PR comment
    is posted — the malfunction self-healed."""
    runtimes, handles = _make_runtimes(approved=True, reviewer_final_texts=[None, _RATIONALE])

    result, fakes = _run(tmp_path, runtimes)

    assert result.success is True
    assert result.pr_number == PR_NUMBER
    # Exactly 2 reviewer runs: the malformed first attempt + the one retry.
    assert len(handles["reviewer"].calls) == 2
    # NO malfunction comment — the retry succeeded.
    assert all(c[0] != "add_pr_comment" for c in fakes["reviewer_adapter"].calls)
    # The retry re-used the SAME seam inputs (prompt identical, MAS §12.7).
    first, retry = handles["reviewer"].calls[0], handles["reviewer"].calls[1]
    assert first["prompt"] == retry["prompt"]
    assert first["ticket_id"] == retry["ticket_id"]
    # The submitted verdict carries the retry's rationale.
    assert (
        "submit_review",
        OWNER,
        REPO,
        PR_NUMBER,
        "APPROVE",
        f"APPROVED: {_RATIONALE}",
    ) in fakes["reviewer_adapter"].calls
    # And the pipeline still merged + transitioned Done.
    assert ("merge_pr", OWNER, REPO, PR_NUMBER, "squash") in fakes["coder_adapter"].calls
    assert fakes["jira"].transitions == [("SFP-224", "51")]


def test_malformed_twice_aborts_with_differentiated_error_and_comment(
    tmp_path: Any,
) -> None:
    """AC: malformed twice → abort carrying the DIFFERENTIATED error string
    (asserted to differ from the 'review not approved' family) + a GitHub
    COMMENT (not a review verdict) noting the malfunction and the retry."""
    runtimes, handles = _make_runtimes(approved=True, reviewer_final_texts=[None, None])

    result, fakes = _run(tmp_path, runtimes)

    assert result.success is False
    assert result.pr_number == PR_NUMBER
    assert result.error == REVIEWER_MALFUNCTION_ERROR
    # The differentiated message must be distinguishable from a code verdict.
    assert result.error != "review not approved: APPROVED"
    assert "review not approved" not in result.error
    # Exactly 2 reviewer runs — the single retry, never a third.
    assert len(handles["reviewer"].calls) == 2
    # A PR conversation COMMENT was posted on the REVIEWER adapter …
    comment_calls = [c for c in fakes["reviewer_adapter"].calls if c[0] == "add_pr_comment"]
    assert comment_calls == [
        ("add_pr_comment", OWNER, REPO, PR_NUMBER, REVIEWER_MALFUNCTION_COMMENT)
    ]
    # … and NO review verdict was ever submitted (a malfunction is not a
    # judgment about the code).
    assert all(c[0] != "submit_review" for c in fakes["reviewer_adapter"].calls)
    # No merge, no Done transition.
    assert all(c[0] != "merge_pr" for c in fakes["coder_adapter"].calls)
    assert fakes["jira"].transitions == []
    # Trace records the comment, then stops — no submit_review after it.
    assert result.trace[-1] == "reviewer_adapter.add_pr_comment"


def test_malformed_twice_on_premerge_rereview_site_aborts_differently(
    tmp_path: Any,
) -> None:
    """AC: BOTH review sites are guarded — the SFP-241 pre-merge re-review
    (a DISMISSED approval for the current head) aborts with the same
    differentiated error and comment, never a 'review not approved' verdict."""
    runtimes, handles = _make_runtimes(
        approved=True,
        # normal review: valid → verdict submitted; re-review: malformed twice.
        reviewer_final_texts=[_RATIONALE, None, None],
    )
    coder_adapter = FakeGitAdapter()
    coder_adapter.reviews = [_review("DISMISSED", HEAD_SHA, review_id=1)]

    result, fakes = _run(tmp_path, runtimes, coder_adapter=coder_adapter)

    assert result.success is False
    assert result.error == REVIEWER_MALFUNCTION_ERROR
    assert "review not approved" not in result.error
    # 3 reviewer runs: the valid normal review + malformed re-review twice.
    assert len(handles["reviewer"].calls) == 3
    # The normal stage's verdict WAS submitted (with its rationale) …
    assert (
        "submit_review",
        OWNER,
        REPO,
        PR_NUMBER,
        "APPROVE",
        f"APPROVED: {_RATIONALE}",
    ) in fakes["reviewer_adapter"].calls
    # … then the malfunction comment, and no second verdict.
    comment_calls = [c for c in fakes["reviewer_adapter"].calls if c[0] == "add_pr_comment"]
    assert len(comment_calls) == 1
    assert comment_calls[0][4] == REVIEWER_MALFUNCTION_COMMENT
    assert len([c for c in fakes["reviewer_adapter"].calls if c[0] == "submit_review"]) == 1
    assert all(c[0] != "merge_pr" for c in fakes["coder_adapter"].calls)
    assert fakes["jira"].transitions == []


def test_malfunction_then_valid_retry_on_premerge_rereview_site_merges(
    tmp_path: Any,
) -> None:
    """AC: the pre-merge re-review site's retry is acted on normally too — a
    malformed re-review followed by a valid APPROVED retry merges + Done."""
    runtimes, handles = _make_runtimes(
        approved=True,
        # normal review: valid; re-review: malformed, then valid.
        reviewer_final_texts=[_RATIONALE, None, _RATIONALE],
    )
    coder_adapter = FakeGitAdapter()
    coder_adapter.reviews = [_review("DISMISSED", HEAD_SHA, review_id=1)]

    result, fakes = _run(tmp_path, runtimes, coder_adapter=coder_adapter)

    assert result.success is True
    assert len(handles["reviewer"].calls) == 3
    assert all(c[0] != "add_pr_comment" for c in fakes["reviewer_adapter"].calls)
    assert ("merge_pr", OWNER, REPO, PR_NUMBER, "squash") in fakes["coder_adapter"].calls
    assert fakes["jira"].transitions == [("SFP-224", "51")]


def test_real_rejection_with_rationale_aborts_standard(tmp_path: Any) -> None:
    """AC: a real rejection (CHANGES_REQUESTED WITH a rationale) is NOT a
    malfunction — it aborts exactly as today, with the standard
    'review not approved' error, no retry, no comment."""
    runtimes, handles = _make_runtimes(
        approved=False, reviewer_final_texts=["The abort path lacks tests."]
    )

    result, fakes = _run(tmp_path, runtimes)

    assert result.success is False
    assert result.error == "review not approved: CHANGES_REQUESTED"
    assert result.error != REVIEWER_MALFUNCTION_ERROR
    # Exactly ONE reviewer run — a valid verdict is never retried.
    assert len(handles["reviewer"].calls) == 1
    # The rejection was submitted as a review verdict carrying its rationale …
    assert (
        "submit_review",
        OWNER,
        REPO,
        PR_NUMBER,
        "REQUEST_CHANGES",
        "CHANGES_REQUESTED: The abort path lacks tests.",
    ) in fakes["reviewer_adapter"].calls
    # … and no malfunction comment was posted.
    assert all(c[0] != "add_pr_comment" for c in fakes["reviewer_adapter"].calls)
    assert all(c[0] != "merge_pr" for c in fakes["coder_adapter"].calls)
    assert fakes["jira"].transitions == []


def test_whitespace_only_rationale_is_malfunction_and_retries(
    tmp_path: Any,
) -> None:
    """A whitespace-only rationale strips to empty → REVIEWER_MALFUNCTION
    (deterministic edge of the validator, driven end-to-end)."""
    runtimes, handles = _make_runtimes(
        approved=True, reviewer_final_texts=["   \n\t  ", _RATIONALE]
    )

    result, fakes = _run(tmp_path, runtimes)

    assert result.success is True
    assert len(handles["reviewer"].calls) == 2
    assert (
        "submit_review",
        OWNER,
        REPO,
        PR_NUMBER,
        "APPROVE",
        f"APPROVED: {_RATIONALE}",
    ) in fakes["reviewer_adapter"].calls


# --------------------------------------------------------------------------- #
# Guard flow end-to-end — rendering-only leg (SFP-252)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "status", [ReviewStatus.APPROVED, ReviewStatus.BLOCKED], ids=lambda s: s.value
)
def test_rendering_equal_rationale_retries_then_valid_verdict(
    tmp_path: Any, status: ReviewStatus
) -> None:
    """SFP-252 AC (end-to-end): a verdict whose final_text is ONLY the
    structured rendering is classified as malfunction on BOTH statuses — the
    review is re-run ONCE, and the retry's PROSE verdict is acted on
    normally (APPROVED → merge + Done; BLOCKED → the STANDARD 'review not
    approved' abort — a real rejection, not the malfunction error)."""
    verdict = _verdict(status)
    rendering = _rendering_of(verdict)
    runtimes, _ = _make_runtimes(approved=True)
    reviewer = FakeRuntime({"reviewer": verdict}, final_texts=[rendering, _RATIONALE])
    runtimes["reviewer"] = reviewer

    result, fakes = _run(tmp_path, runtimes)

    # The malformed first attempt was retried exactly once, then accepted.
    assert len(reviewer.calls) == 2
    assert all(c[0] != "add_pr_comment" for c in fakes["reviewer_adapter"].calls)
    assert (
        "submit_review",
        OWNER,
        REPO,
        PR_NUMBER,
        "APPROVE" if status is ReviewStatus.APPROVED else "REQUEST_CHANGES",
        f"{status.value}: {_RATIONALE}",
    ) in fakes["reviewer_adapter"].calls
    if status is ReviewStatus.APPROVED:
        assert result.success is True
        assert ("merge_pr", OWNER, REPO, PR_NUMBER, "squash") in fakes["coder_adapter"].calls
        assert fakes["jira"].transitions == [("SFP-224", "51")]
    else:
        # A valid BLOCKED retry is a REAL code rejection: the standard abort,
        # never the malfunction error, and no merge / no Done.
        assert result.success is False
        assert result.error == "review not approved: BLOCKED"
        assert result.error != REVIEWER_MALFUNCTION_ERROR
        assert all(c[0] != "merge_pr" for c in fakes["coder_adapter"].calls)
        assert fakes["jira"].transitions == []


def test_prose_rationale_with_all_false_gates_aborts_standard_not_malfunction(
    tmp_path: Any,
) -> None:
    """SFP-252 AC (end-to-end): a BLOCKED verdict with ALL six gates false and
    zero textual findings — but WITH reviewer prose — is NOT a malfunction:
    exactly ONE reviewer run, the standard 'review not approved' abort, the
    verdict submitted carrying its rationale, and no PR comment. Gate values
    never enter the malfunction decision."""
    verdict = _verdict(ReviewStatus.BLOCKED, gates=False)
    prose = "Every gate fails; nothing here meets the spec. Rejection stands."
    runtimes, _ = _make_runtimes(approved=True)
    reviewer = FakeRuntime({"reviewer": verdict}, final_texts=[prose])
    runtimes["reviewer"] = reviewer

    result, fakes = _run(tmp_path, runtimes)

    assert result.success is False
    assert result.error == "review not approved: BLOCKED"
    assert result.error != REVIEWER_MALFUNCTION_ERROR
    # Exactly ONE reviewer run — the prose verdict was never retried.
    assert len(reviewer.calls) == 1
    assert all(c[0] != "add_pr_comment" for c in fakes["reviewer_adapter"].calls)
    assert (
        "submit_review",
        OWNER,
        REPO,
        PR_NUMBER,
        "REQUEST_CHANGES",
        f"BLOCKED: {prose}",
    ) in fakes["reviewer_adapter"].calls
    assert all(c[0] != "merge_pr" for c in fakes["coder_adapter"].calls)
    assert fakes["jira"].transitions == []


@pytest.mark.parametrize(
    "status", [ReviewStatus.APPROVED, ReviewStatus.BLOCKED], ids=lambda s: s.value
)
def test_rendering_only_rationale_twice_aborts_with_comment(
    tmp_path: Any, status: ReviewStatus
) -> None:
    """SFP-252 AC: the rendering-only malfunction twice → the SAME recovery
    as the empty case — abort with REVIEWER_MALFUNCTION_ERROR (distinct from
    the 'review not approved: <STATUS>' family, on BOTH statuses) and the
    REVIEWER_MALFUNCTION_COMMENT posted to the PR conversation; no verdict is
    ever submitted."""
    verdict = _verdict(status)
    rendering = _rendering_of(verdict)
    runtimes, _ = _make_runtimes(approved=True)
    reviewer = FakeRuntime({"reviewer": verdict}, final_texts=[rendering, rendering])
    runtimes["reviewer"] = reviewer

    result, fakes = _run(tmp_path, runtimes)

    assert result.success is False
    assert result.error == REVIEWER_MALFUNCTION_ERROR
    assert result.error != f"review not approved: {status.value}"
    assert "review not approved" not in result.error
    # The single retry, never a third run.
    assert len(reviewer.calls) == 2
    # The comment was posted …
    comment_calls = [c for c in fakes["reviewer_adapter"].calls if c[0] == "add_pr_comment"]
    assert comment_calls == [
        ("add_pr_comment", OWNER, REPO, PR_NUMBER, REVIEWER_MALFUNCTION_COMMENT)
    ]
    # … and no verdict, no merge, no Done.
    assert all(c[0] != "submit_review" for c in fakes["reviewer_adapter"].calls)
    assert all(c[0] != "merge_pr" for c in fakes["coder_adapter"].calls)
    assert fakes["jira"].transitions == []


# --------------------------------------------------------------------------- #
# Real-runtime path — the SFP-122 reproducer shape (SFP-252)
# --------------------------------------------------------------------------- #


def test_guard_rendering_matches_runtime_fallback_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SFP-252 identity pin: the REAL runtime's structured-only ``final_text``
    is EXACTLY the string the guard computes as the expected rendering
    (:func:`_structured_rendering`'s body) — byte-identical by construction
    (both render the validated contract dump through the shared
    ``render_structured_verdict``). If either side drifts, this fails loudly
    before the equality leg silently stops firing."""
    structured = _reviewer_output(approved=True)
    runtime = _real_reviewer_runtime(monkeypatch, _StructuredOnlyQueryFn(structured))

    res = runtime.run(
        AgentRunRequest(agent="reviewer", ticket_id="SFP-224", prompt="review", context={})
    )

    assert res.success is True
    expected = render_structured_verdict(
        ReviewerOutput.model_validate(structured).model_dump(mode="json")
    )
    assert res.final_text == expected
    # And that string is what classifies the verdict as rendering-only.
    assert is_malformed_rationale(res.final_text, expected) is True


def test_structured_only_path_is_malfunction_through_real_runtime(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SFP-252 (supersedes the SFP-249 F1 expectation): a review from the
    structured-only path — the REAL ``ClaudeAgentRuntime`` with
    ``enforce_schema=True`` whose ``ResultMessage`` carries
    ``structured_output`` and ``result=None``, exactly what the SDK's
    output_format enforcement produces on a prose-less run — has a final_text
    that is ONLY the deterministic rendering. That is the SFP-122 reproducer
    shape (first run 2026-09-12, PR #167 — see
    ``logs/run-SFP-122-20260911T233343Z.log``, cited as documentation only):
    it now classifies as REVIEWER_MALFUNCTION — retried once, and the second
    prose-less verdict aborts with the differentiated error + PR comment. No
    verdict is submitted. (The abort itself pins the runtime↔guard
    byte-identity: the equality leg only fires on the runtime's own bytes.)"""
    structured = _reviewer_output(approved=True)
    qfn = _StructuredOnlyQueryFn(structured)
    runtimes, _ = _make_runtimes(approved=True)
    runtimes["reviewer"] = _real_reviewer_runtime(monkeypatch, qfn)

    result, fakes = _run(tmp_path, runtimes)

    assert result.success is False
    assert result.error == REVIEWER_MALFUNCTION_ERROR
    assert "review not approved" not in result.error
    # Exactly 2 reviewer runs: the malformed attempt + the single retry.
    assert len(qfn.calls) == 2
    # The PR conversation comment was posted …
    comment_calls = [c for c in fakes["reviewer_adapter"].calls if c[0] == "add_pr_comment"]
    assert comment_calls == [
        ("add_pr_comment", OWNER, REPO, PR_NUMBER, REVIEWER_MALFUNCTION_COMMENT)
    ]
    # … no verdict, no merge, no Done.
    assert all(c[0] != "submit_review" for c in fakes["reviewer_adapter"].calls)
    assert all(c[0] != "merge_pr" for c in fakes["coder_adapter"].calls)
    assert fakes["jira"].transitions == []


def test_structured_path_with_reviewer_prose_merges(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SFP-252 healthy-path pin: the widened guard does NOT fire when the
    structured path carries reviewer-authored prose — a ``ResultMessage`` with
    BOTH ``result`` text and ``structured_output`` runs the review exactly
    ONCE, submits the verdict with the prose rationale, and merges + Done."""
    structured = _reviewer_output(approved=True)
    prose = "Matches the PRSpec; all gates pass with sound tests."
    qfn = _StructuredWithTextQueryFn(prose, structured)
    runtimes, _ = _make_runtimes(approved=True)
    runtimes["reviewer"] = _real_reviewer_runtime(monkeypatch, qfn)

    result, fakes = _run(tmp_path, runtimes)

    assert result.success is True
    # Exactly ONE reviewer run — the prose verdict was never retried.
    assert len(qfn.calls) == 1
    assert all(c[0] != "add_pr_comment" for c in fakes["reviewer_adapter"].calls)
    assert (
        "submit_review",
        OWNER,
        REPO,
        PR_NUMBER,
        "APPROVE",
        f"APPROVED: {prose}",
    ) in fakes["reviewer_adapter"].calls
    assert ("merge_pr", OWNER, REPO, PR_NUMBER, "squash") in fakes["coder_adapter"].calls
    assert fakes["jira"].transitions == [("SFP-224", "51")]


# --------------------------------------------------------------------------- #
# SFP-253 — the malfunction path against a REUSED PR (resume idempotency)
# --------------------------------------------------------------------------- #


def test_malfunction_comment_lands_on_the_reused_pr_number(tmp_path: Any) -> None:
    """SFP-253 regression pin (AC6): the SFP-249 malfunction-comment path reads
    the PR from the run state — on a run that ADOPTED an existing open PR
    (GitHub's typed 422 + exactly-one lookup hit), the conversation comment
    lands on the REUSED PR number. No new PR exists to comment on instead."""
    runtimes, _ = _make_runtimes(approved=True, reviewer_final_texts=[None, None])
    coder_adapter = FakeGitAdapter(create_pr_exc=_already_exists_error(), open_prs=[_reused_pr()])

    result, fakes = _run(tmp_path, runtimes, coder_adapter=coder_adapter)

    assert result.success is False
    # The abort carries the REUSED PR number (the run's PR after adoption).
    assert result.pr_number == _REUSED_PR_NUMBER
    assert result.error == REVIEWER_MALFUNCTION_ERROR
    # The conversation comment landed on the REUSED PR number.
    comment_calls = [c for c in fakes["reviewer_adapter"].calls if c[0] == "add_pr_comment"]
    assert comment_calls == [
        ("add_pr_comment", OWNER, REPO, _REUSED_PR_NUMBER, REVIEWER_MALFUNCTION_COMMENT)
    ]
    # No verdict was ever submitted and nothing merged on any PR number.
    assert all(c[0] != "submit_review" for c in fakes["reviewer_adapter"].calls)
    assert all(c[0] != "merge_pr" for c in coder_adapter.calls)
