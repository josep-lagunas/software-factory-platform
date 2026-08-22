"""Tests for the coding/review stage drivers + the ID-068 rework loop (SFP-139).

Covers the three new edges driven in ``transitions.py``:

- ``READY_FOR_CODING → CODING_IN_PROGRESS`` — fired by a *coding job started*
  fact (a ``CodingJobUpdated``-style observation whose ``status`` is
  ``"running"``, the value pinned by the landed SFP-219 serde contract).
- ``CODING_IN_PROGRESS → REVIEW_IN_PROGRESS`` — fired by a *PR created /
  review requested* fact (the Coder's implementation-evidence record: producer
  ``"coder"`` with a non-empty ``branch_name``, the landed ``CoderOutput``
  contract fields).
- ``REVIEW_IN_PROGRESS → CODING_IN_PROGRESS`` (the ID-068 rework loop) — fired
  by a *changes-requested review* fact (a ``ReviewUpdated``-style observation
  whose ``review_status`` is ``"CHANGES_REQUESTED"`` from the landed
  :class:`~sfp_contracts.agents.reviewer.ReviewStatus` vocabulary). Rework is
  normal progression: never ``FAILED``, never an escalation.

Plus the §8.8 non-move recording: an ``APPROVED`` review does NOT loop back to
coding (the merge stage it selects is SFP-140's concern) and is instead
recorded as a returned non-move; illegal moves raise straight from the SFP-137
table guard; the drivers add no states, duplicate no table data, and stay pure
and deterministic (AP-011).
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest
from orchestrator.domain.workflow.state_machine import (
    TRANSITIONS,
    IllegalTransitionError,
    WorkflowDecision,
    transition,
)
from orchestrator.domain.workflow.states import STATES, WorkflowState
from orchestrator.domain.workflow.transitions import (
    CHANGES_REQUESTED_STATUS,
    CODER_AGENT,
    CODING_JOB_RUNNING_STATUS,
    CODING_STAGE_POLICY,
    CODING_STAGE_SOURCE,
    CODING_STAGE_TARGET,
    REVIEW_STAGE_POLICY,
    REVIEW_STAGE_SOURCE,
    REVIEW_STAGE_TARGET,
    REWORK_POLICY,
    REWORK_SOURCE,
    REWORK_TARGET,
    changes_requested_review_fact,
    coding_job_started_fact,
    drive_coding_stage,
    drive_review_stage,
    drive_rework_loop,
    pr_created_fact,
)

#: The canonical coding-job-started fact: the ``status`` of a
#: ``CodingJobUpdated``-style observation (``job_id`` + ``status``, per the
#: landed SFP-219 serde contract).
STARTED_CODING_FACT = "running"

#: The canonical PR-created / review-requested fact: ``(agent, branch_name)``
#: — the landed ``CoderOutput`` contract's producer + branch fields.
PR_FACT: tuple[str, str] = ("coder", "sfp-sfp-139-coding-review")

#: The canonical changes-requested review fact: ``(pr_number, review_status)``
#: — the landed ``ReviewUpdated`` observation shape, with the verdict taken
#: from the landed ``ReviewStatus`` vocabulary.
REWORK_FACT: tuple[int, str] = (7, "CHANGES_REQUESTED")


# --- The coding-job-started fact predicate -------------------------------------


@pytest.mark.parametrize(
    ("job_status", "expected"),
    [
        # The one running-state value pinned by the landed SFP-219 serde
        # contract for CodingJobUpdated ({"job_id": ..., "status": "running"}).
        ("running", True),
        # Every other CodingJobUpdated vocabulary value is *not* a started
        # fact: the job is queued / complete / etc. — not started.
        ("queued", False),
        ("complete", False),
        ("completed", False),
        ("failed", False),
        ("cancelled", False),
        ("", False),
        (None, False),
        # Case is part of the vocabulary: not a lenient substring match.
        ("RUNNING", False),
        ("Running", False),
    ],
)
def test_coding_job_started_fact_predicate(job_status: str | None, expected: bool) -> None:
    assert coding_job_started_fact(job_status=job_status) is expected


def test_coding_job_running_status_is_the_landed_pinned_value() -> None:
    assert CODING_JOB_RUNNING_STATUS == "running"


# --- Happy path: coding job started → CODING_IN_PROGRESS ------------------------


def test_started_coding_job_moves_to_coding_in_progress() -> None:
    new_state, decision = drive_coding_stage(
        WorkflowState.READY_FOR_CODING,
        coding_fact=STARTED_CODING_FACT,
    )
    assert new_state is WorkflowState.CODING_IN_PROGRESS
    assert decision.previous_state is WorkflowState.READY_FOR_CODING
    assert decision.resulting_state is WorkflowState.CODING_IN_PROGRESS
    # ID-013: plain-string companions.
    assert decision.previous_state_name == "READY_FOR_CODING"
    assert decision.resulting_state_name == "CODING_IN_PROGRESS"


def test_started_coding_job_decision_is_the_engine_produced_record() -> None:
    # The move is delegated to the SFP-137 engine: the returned decision is
    # exactly what transition() produces for this edge — the driver neither
    # re-implements the guard nor fabricates its own move record.
    _, via_engine = transition(
        CODING_STAGE_SOURCE,
        CODING_STAGE_TARGET,
        reason="probe",
        applied_policy=CODING_STAGE_POLICY,
    )
    _, via_driver = drive_coding_stage(
        WorkflowState.READY_FOR_CODING,
        coding_fact=STARTED_CODING_FACT,
    )
    assert via_driver.previous_state == via_engine.previous_state
    assert via_driver.resulting_state == via_engine.resulting_state
    assert via_driver.applied_policy == CODING_STAGE_POLICY == via_engine.applied_policy
    assert via_driver.business_facts_considered == ("coding-job-fact:running",)
    assert via_driver.aggregate_changes == ("tickets.workflow_status",)


def test_started_coding_job_decision_is_immutable() -> None:
    _, decision = drive_coding_stage(
        WorkflowState.READY_FOR_CODING,
        coding_fact=STARTED_CODING_FACT,
    )
    with pytest.raises(ValueError, match="frozen"):
        decision.reason = "mutated"


def test_started_coding_job_decision_emits_no_commands() -> None:
    # §8.6: commands are outputs only and never modify workflow state. The
    # driver emits none itself — recording a command would be the caller's
    # (SFP-142 policy / publisher wrapper) decision, never a state change.
    _, decision = drive_coding_stage(
        WorkflowState.READY_FOR_CODING,
        coding_fact=STARTED_CODING_FACT,
    )
    assert decision.commands_emitted == ()


# --- Coding fact absent / not started → NO transition + recorded non-move ------


def test_absent_coding_fact_holds_state_and_records_non_move() -> None:
    new_state, decision = drive_coding_stage(WorkflowState.READY_FOR_CODING)
    assert new_state is WorkflowState.READY_FOR_CODING
    assert decision.previous_state is WorkflowState.READY_FOR_CODING
    assert decision.resulting_state is WorkflowState.READY_FOR_CODING
    assert decision.business_facts_considered == ("coding-job-fact:absent",)
    assert "no coding job" in decision.reason
    # The non-move is returned (never swallowed) and carries the applied rule.
    assert decision.applied_policy == CODING_STAGE_POLICY


@pytest.mark.parametrize("job_status", ["queued", "complete", "failed", "cancelled"])
def test_not_started_coding_fact_holds_state_and_records_non_move(
    job_status: str,
) -> None:
    new_state, decision = drive_coding_stage(
        WorkflowState.READY_FOR_CODING,
        coding_fact=job_status,
    )
    assert new_state is WorkflowState.READY_FOR_CODING
    assert decision.resulting_state is WorkflowState.READY_FOR_CODING
    # Every not-yet-started fact still names the fact it considered (§8.8).
    assert decision.business_facts_considered == (f"coding-job-fact:{job_status}",)
    assert "not started" in decision.reason


def test_coding_non_move_identifiers_are_deterministic() -> None:
    _, first = drive_coding_stage(
        WorkflowState.READY_FOR_CODING,
        coding_fact="queued",
    )
    _, second = drive_coding_stage(
        WorkflowState.READY_FOR_CODING,
        coding_fact="queued",
    )
    assert first.business_facts_considered == ("coding-job-fact:queued",)
    assert first == second


def _states_where_the_move_is_table_illegal(
    target: WorkflowState,
) -> list[WorkflowState]:
    """Every §8.4 state from which ``target`` is NOT a legal table move.

    Derived from the landed SFP-137 table as data, so the wrong-state tests
    below encode the actual guarantee — the driver raises iff the table says
    the move is illegal — and automatically cover every table-legal overlap
    (WAITING_FOR_USER resume edges, and the REVIEW_IN_PROGRESS rework edge
    that also targets CODING_IN_PROGRESS) without hand-maintaining a list.
    """
    return sorted(
        (state for state in STATES if target not in TRANSITIONS[state]),
        key=lambda s: s.name,
    )


def test_started_coding_job_from_wrong_state_raises() -> None:
    # The driver only ever drives the single coding-start edge; a workflow at
    # any state where that move is table-illegal must raise straight from the
    # SFP-137 table guard — never an implicit or best-effort move.
    wrong_states = _states_where_the_move_is_table_illegal(CODING_STAGE_TARGET)
    assert WorkflowState.READY_FOR_PR_SPECIFICATION in wrong_states
    assert WorkflowState.REVIEW_IN_PROGRESS not in wrong_states  # rework edge
    assert WorkflowState.WAITING_FOR_USER not in wrong_states  # resume edge
    assert wrong_states  # sanity: the table still forbids somewhere
    for state in wrong_states:
        with pytest.raises(IllegalTransitionError) as excinfo:
            drive_coding_stage(state, coding_fact=STARTED_CODING_FACT)
        assert excinfo.value.current_state is state
        assert excinfo.value.attempted_target is CODING_STAGE_TARGET


# --- The PR-created / review-requested fact predicate --------------------------


@pytest.mark.parametrize(
    ("agent", "branch_name", "expected"),
    [
        ("coder", "sfp-sfp-139-branch", True),
        ("coder", "any-branch", True),
        # Wrong producer: only the Coder's output opens the PR / requests
        # review (§8.6 — the workflow advances on the producer's event).
        ("planner", "sfp-sfp-139-branch", False),
        ("reviewer", "sfp-sfp-139-branch", False),
        ("orchestrator", "sfp-sfp-139-branch", False),
        (None, "sfp-sfp-139-branch", False),
        # No branch yet: the Coder has not finished, so no PR exists.
        ("coder", "", False),
        ("coder", None, False),
        (None, None, False),
    ],
)
def test_pr_created_fact_predicate(
    agent: str | None,
    branch_name: str | None,
    expected: bool,
) -> None:
    assert pr_created_fact(agent=agent, branch_name=branch_name) is expected


def test_coder_agent_constant_is_the_canonical_identifier() -> None:
    assert CODER_AGENT == "coder"


# --- Happy path: PR created / review requested → REVIEW_IN_PROGRESS ------------


def test_pr_created_moves_to_review_in_progress() -> None:
    new_state, decision = drive_review_stage(
        WorkflowState.CODING_IN_PROGRESS,
        pr_fact=PR_FACT,
    )
    assert new_state is WorkflowState.REVIEW_IN_PROGRESS
    assert decision.previous_state is WorkflowState.CODING_IN_PROGRESS
    assert decision.resulting_state is WorkflowState.REVIEW_IN_PROGRESS
    assert decision.previous_state_name == "CODING_IN_PROGRESS"
    assert decision.resulting_state_name == "REVIEW_IN_PROGRESS"


def test_pr_created_decision_is_the_engine_produced_record() -> None:
    _, via_engine = transition(
        REVIEW_STAGE_SOURCE,
        REVIEW_STAGE_TARGET,
        reason="probe",
        applied_policy=REVIEW_STAGE_POLICY,
    )
    _, via_driver = drive_review_stage(
        WorkflowState.CODING_IN_PROGRESS,
        pr_fact=PR_FACT,
    )
    assert via_driver.previous_state == via_engine.previous_state
    assert via_driver.resulting_state == via_engine.resulting_state
    assert via_driver.applied_policy == REVIEW_STAGE_POLICY == via_engine.applied_policy
    assert via_driver.business_facts_considered == (
        f"pr-fact:{CODER_AGENT}:sfp-sfp-139-coding-review",
    )
    assert via_driver.aggregate_changes == ("tickets.workflow_status",)


def test_pr_created_decision_is_immutable() -> None:
    _, decision = drive_review_stage(
        WorkflowState.CODING_IN_PROGRESS,
        pr_fact=PR_FACT,
    )
    with pytest.raises(ValueError, match="frozen"):
        decision.reason = "mutated"


# --- PR fact absent / incomplete → NO transition + recorded non-move -----------


def test_absent_pr_fact_holds_state_and_records_non_move() -> None:
    new_state, decision = drive_review_stage(WorkflowState.CODING_IN_PROGRESS)
    assert new_state is WorkflowState.CODING_IN_PROGRESS
    assert decision.resulting_state is WorkflowState.CODING_IN_PROGRESS
    assert decision.business_facts_considered == ("pr-fact:absent",)
    assert "no PR-created fact" in decision.reason
    assert decision.applied_policy == REVIEW_STAGE_POLICY


@pytest.mark.parametrize(
    ("agent", "branch_name"),
    [
        ("coder", ""),  # Coder finished? No branch yet.
        ("coder", None),
        ("planner", "sfp-sfp-139-branch"),  # wrong producer
        ("reviewer", "sfp-sfp-139-branch"),
        (None, "sfp-sfp-139-branch"),
        (None, None),
    ],
)
def test_incomplete_pr_fact_holds_state_and_records_non_move(
    agent: str | None,
    branch_name: str | None,
) -> None:
    fact: tuple[str | None, str | None] = (agent, branch_name)
    new_state, decision = drive_review_stage(
        WorkflowState.CODING_IN_PROGRESS,
        pr_fact=fact,
    )
    assert new_state is WorkflowState.CODING_IN_PROGRESS
    assert decision.resulting_state is WorkflowState.CODING_IN_PROGRESS
    # §8.8: the non-move names the fact it considered — never a silent branch.
    assert len(decision.business_facts_considered) == 1
    assert decision.business_facts_considered[0].startswith("pr-fact:")
    assert "absent/incomplete" in decision.reason


def test_pr_non_move_identifiers_are_deterministic() -> None:
    fact: tuple[str | None, str | None] = ("coder", None)
    _, first = drive_review_stage(WorkflowState.CODING_IN_PROGRESS, pr_fact=fact)
    _, second = drive_review_stage(WorkflowState.CODING_IN_PROGRESS, pr_fact=fact)
    assert first.business_facts_considered == ("pr-fact:coder:no-branch",)
    assert first == second


def test_pr_created_fact_from_wrong_state_raises() -> None:
    wrong_states = _states_where_the_move_is_table_illegal(REVIEW_STAGE_TARGET)
    assert WorkflowState.READY_FOR_CODING in wrong_states
    assert WorkflowState.REVIEW_IN_PROGRESS in wrong_states  # no review→review
    assert wrong_states  # sanity: the table still forbids somewhere
    for state in wrong_states:
        with pytest.raises(IllegalTransitionError) as excinfo:
            drive_review_stage(state, pr_fact=PR_FACT)
        assert excinfo.value.current_state is state
        assert excinfo.value.attempted_target is REVIEW_STAGE_TARGET


# --- The changes-requested review fact predicate -------------------------------


@pytest.mark.parametrize(
    ("review_status", "expected"),
    [
        # The ID-068 rework verdict, from the landed ReviewStatus vocabulary.
        ("CHANGES_REQUESTED", True),
        # APPROVED selects the merge stage (SFP-140) — NOT this driver's move.
        ("APPROVED", False),
        # The other landed ReviewStatus verdicts are not this driver's concern
        # either (BLOCKED / NEEDS_HUMAN_DECISION).
        ("BLOCKED", False),
        ("NEEDS_HUMAN_DECISION", False),
        ("", False),
        (None, False),
        # Case is part of the vocabulary: not a lenient match.
        ("changes_requested", False),
        ("ChangesRequested", False),
    ],
)
def test_changes_requested_review_fact_predicate(
    review_status: str | None,
    expected: bool,
) -> None:
    assert changes_requested_review_fact(review_status=review_status) is expected


def test_changes_requested_status_is_the_landed_review_status_value() -> None:
    # Pinned against the landed contract vocabulary, not a local invention.
    from sfp_contracts.agents.reviewer import ReviewStatus

    assert CHANGES_REQUESTED_STATUS == ReviewStatus.CHANGES_REQUESTED.value


# --- Happy path: CHANGES_REQUESTED → the ID-068 rework loop ---------------------


def test_changes_requested_moves_back_to_coding_in_progress() -> None:
    new_state, decision = drive_rework_loop(
        WorkflowState.REVIEW_IN_PROGRESS,
        review_fact=REWORK_FACT,
    )
    assert new_state is WorkflowState.CODING_IN_PROGRESS
    assert decision.previous_state is WorkflowState.REVIEW_IN_PROGRESS
    assert decision.resulting_state is WorkflowState.CODING_IN_PROGRESS
    assert decision.previous_state_name == "REVIEW_IN_PROGRESS"
    assert decision.resulting_state_name == "CODING_IN_PROGRESS"


def test_rework_decision_is_the_engine_produced_record() -> None:
    _, via_engine = transition(
        REWORK_SOURCE,
        REWORK_TARGET,
        reason="probe",
        applied_policy=REWORK_POLICY,
    )
    _, via_driver = drive_rework_loop(
        WorkflowState.REVIEW_IN_PROGRESS,
        review_fact=REWORK_FACT,
    )
    assert via_driver.previous_state == via_engine.previous_state
    assert via_driver.resulting_state == via_engine.resulting_state
    assert via_driver.applied_policy == REWORK_POLICY == via_engine.applied_policy
    assert via_driver.business_facts_considered == ("review-fact:7:CHANGES_REQUESTED",)
    assert via_driver.aggregate_changes == ("tickets.workflow_status",)


def test_rework_decision_is_immutable() -> None:
    _, decision = drive_rework_loop(
        WorkflowState.REVIEW_IN_PROGRESS,
        review_fact=REWORK_FACT,
    )
    with pytest.raises(ValueError, match="frozen"):
        decision.reason = "mutated"


def test_rework_is_a_table_edge_not_a_new_move() -> None:
    # ID-068 lives in the SFP-137 table as data — the driver requests it, it
    # never declares a new edge of its own.
    assert REWORK_TARGET in TRANSITIONS[REWORK_SOURCE]
    assert REWORK_SOURCE is WorkflowState.REVIEW_IN_PROGRESS
    assert REWORK_TARGET is WorkflowState.CODING_IN_PROGRESS


# --- ID-068: rework never enters FAILED and never escalates --------------------


def test_rework_never_produces_the_failed_state() -> None:
    # ID-068: a CHANGES_REQUESTED review is the expected coder↔reviewer
    # quality loop — normal progression, never a failure. The only state this
    # driver can ever produce from the rework fact is CODING_IN_PROGRESS.
    for fact in (
        REWORK_FACT,
        (7, "CHANGES_REQUESTED"),
        (99, "CHANGES_REQUESTED"),
    ):
        new_state, decision = drive_rework_loop(
            WorkflowState.REVIEW_IN_PROGRESS,
            review_fact=fact,
        )
        assert new_state is not WorkflowState.FAILED
        assert decision.resulting_state is not WorkflowState.FAILED


def test_rework_has_no_failed_or_escalation_code_path() -> None:
    # Structural guarantee (ID-068): the rework driver's *code* never
    # references FAILED or any escalation vocabulary, and its only requested
    # target is CODING_IN_PROGRESS. Docstrings are stripped first so the
    # assertion inspects executable statements, not prose. If someone later
    # adds an escalation path, this test — not a silent behavior change —
    # notices first.
    import ast

    import orchestrator.domain.workflow.transitions as transitions_module

    # Normalize to executable statements only: ast.unparse drops both the
    # docstring and every comment, so the ID-068 *narrative* ("never FAILED,
    # never an escalation") cannot satisfy this check — only real code could.
    func = ast.parse(
        inspect.getsource(transitions_module.drive_rework_loop),
    ).body[0]
    assert isinstance(func, ast.FunctionDef)
    # Drop the docstring node, then unparse what remains.
    body_nodes = func.body
    if (
        body_nodes
        and isinstance(body_nodes[0], ast.Expr)
        and isinstance(body_nodes[0].value, ast.Constant)
    ):
        body_nodes = body_nodes[1:]
    body = "\n".join(ast.unparse(node) for node in body_nodes)

    assert "FAILED" not in body
    assert "escalat" not in body.lower()
    # The only target this driver's code ever names is the rework target.
    assert "REWORK_TARGET" in body
    assert "WorkflowState.FAILED" not in body

    # Exhaustive over the landed ReviewStatus vocabulary: only
    # CHANGES_REQUESTED moves; every other verdict holds the state (§8.8).
    from sfp_contracts.agents.reviewer import ReviewStatus

    for verdict in ReviewStatus:
        new_state, decision = drive_rework_loop(
            WorkflowState.REVIEW_IN_PROGRESS,
            review_fact=(7, verdict.value),
        )
        if verdict is ReviewStatus.CHANGES_REQUESTED:
            assert new_state is WorkflowState.CODING_IN_PROGRESS
            assert decision.resulting_state is WorkflowState.CODING_IN_PROGRESS
        else:
            assert new_state is WorkflowState.REVIEW_IN_PROGRESS
            assert decision.resulting_state is WorkflowState.REVIEW_IN_PROGRESS
            assert decision.applied_policy == REWORK_POLICY


def test_rework_loop_repeats_indefinitely_without_failing() -> None:
    # ID-068: rework may loop any number of times — review → coding → review →
    # coding... — and the workflow never degrades into FAILED or any
    # escalation. The loop is bounded only by the facts, never by a counter.
    state = WorkflowState.REVIEW_IN_PROGRESS
    for _ in range(5):
        state, move = drive_rework_loop(state, review_fact=REWORK_FACT)
        assert state is WorkflowState.CODING_IN_PROGRESS
        assert move.resulting_state is not WorkflowState.FAILED
        state, to_review = drive_review_stage(state, pr_fact=PR_FACT)
        assert state is WorkflowState.REVIEW_IN_PROGRESS
        assert to_review.resulting_state is not WorkflowState.FAILED


# --- The APPROVED non-move (§8.8) — merge stage is SFP-140's concern -----------


def test_approved_review_does_not_loop_back_to_coding() -> None:
    # An APPROVED review fact does NOT drive this stage: it must not move the
    # workflow back to CODING_IN_PROGRESS (or anywhere else this module owns).
    new_state, decision = drive_rework_loop(
        WorkflowState.REVIEW_IN_PROGRESS,
        review_fact=(7, "APPROVED"),
    )
    assert new_state is WorkflowState.REVIEW_IN_PROGRESS
    assert decision.resulting_state is WorkflowState.REVIEW_IN_PROGRESS
    # The non-move is returned, never swallowed (§8.8).
    assert decision.previous_state is WorkflowState.REVIEW_IN_PROGRESS
    assert decision.applied_policy == REWORK_POLICY
    assert decision.business_facts_considered == ("review-fact:7:APPROVED",)
    # The reason names the actual verdict and defers the merge stage.
    assert "APPROVED" not in decision.reason or "SFP-140" in decision.reason
    assert "SFP-140" in decision.reason


def test_absent_review_fact_holds_state_and_records_non_move() -> None:
    new_state, decision = drive_rework_loop(WorkflowState.REVIEW_IN_PROGRESS)
    assert new_state is WorkflowState.REVIEW_IN_PROGRESS
    assert decision.resulting_state is WorkflowState.REVIEW_IN_PROGRESS
    assert decision.business_facts_considered == ("review-fact:absent",)
    assert "no review fact" in decision.reason
    assert decision.applied_policy == REWORK_POLICY


def test_review_non_move_identifiers_are_deterministic() -> None:
    _, first = drive_rework_loop(
        WorkflowState.REVIEW_IN_PROGRESS,
        review_fact=(7, "APPROVED"),
    )
    _, second = drive_rework_loop(
        WorkflowState.REVIEW_IN_PROGRESS,
        review_fact=(7, "APPROVED"),
    )
    assert first.business_facts_considered == ("review-fact:7:APPROVED",)
    assert first == second


def test_changes_requested_from_wrong_state_raises() -> None:
    wrong_states = _states_where_the_move_is_table_illegal(REWORK_TARGET)
    assert WorkflowState.CODING_IN_PROGRESS in wrong_states  # no self-edge
    assert WorkflowState.READY_FOR_PR_SPECIFICATION in wrong_states
    assert WorkflowState.REVIEW_IN_PROGRESS not in wrong_states  # the rework edge
    assert WorkflowState.WAITING_FOR_USER not in wrong_states  # resume edge
    assert wrong_states  # sanity: the table still forbids somewhere
    for state in wrong_states:
        with pytest.raises(IllegalTransitionError) as excinfo:
            drive_rework_loop(state, review_fact=REWORK_FACT)
        assert excinfo.value.current_state is state
        assert excinfo.value.attempted_target is REWORK_TARGET


def test_waiting_for_user_rework_resume_edge_is_table_legal() -> None:
    # Guard the exclusion above: WAITING_FOR_USER -> CODING_IN_PROGRESS must
    # remain a *table* edge (resume semantics — a review-stage answer may
    # itself request rework), so if the SFP-137 table ever drops it, this
    # test — not a silent behavior change — notices first.
    assert REWORK_TARGET in TRANSITIONS[WorkflowState.WAITING_FOR_USER]
    assert REVIEW_STAGE_TARGET in TRANSITIONS[WorkflowState.WAITING_FOR_USER]


# --- No new states, no table edits ---------------------------------------------


def test_drivers_add_no_workflow_states() -> None:
    # SFP-139 required no transition-table change: all three edges were
    # already legal in the landed SFP-137 table (verified at read time —
    # the rework edge is ID-068, landed with the table itself).
    assert len(STATES) == 10
    for edge in (
        (CODING_STAGE_SOURCE, CODING_STAGE_TARGET),
        (REVIEW_STAGE_SOURCE, REVIEW_STAGE_TARGET),
        (REWORK_SOURCE, REWORK_TARGET),
    ):
        assert edge[1] in TRANSITIONS[edge[0]]


def test_all_three_edges_exist_in_the_landed_table() -> None:
    assert CODING_STAGE_SOURCE is WorkflowState.READY_FOR_CODING
    assert CODING_STAGE_TARGET is WorkflowState.CODING_IN_PROGRESS
    assert REVIEW_STAGE_SOURCE is WorkflowState.CODING_IN_PROGRESS
    assert REVIEW_STAGE_TARGET is WorkflowState.REVIEW_IN_PROGRESS
    assert REWORK_SOURCE is WorkflowState.REVIEW_IN_PROGRESS
    assert REWORK_TARGET is WorkflowState.CODING_IN_PROGRESS


# --- Purity + determinism (AP-011) ---------------------------------------------


def test_all_three_drivers_are_deterministic() -> None:
    coding_first = drive_coding_stage(
        WorkflowState.READY_FOR_CODING,
        coding_fact=STARTED_CODING_FACT,
    )
    coding_second = drive_coding_stage(
        WorkflowState.READY_FOR_CODING,
        coding_fact=STARTED_CODING_FACT,
    )
    assert coding_first[0] is coding_second[0]
    assert coding_first[1] == coding_second[1]
    assert coding_first[1].to_json() == coding_second[1].to_json()

    review_first = drive_review_stage(
        WorkflowState.CODING_IN_PROGRESS,
        pr_fact=PR_FACT,
    )
    review_second = drive_review_stage(
        WorkflowState.CODING_IN_PROGRESS,
        pr_fact=PR_FACT,
    )
    assert review_first == review_second

    rework_first = drive_rework_loop(
        WorkflowState.REVIEW_IN_PROGRESS,
        review_fact=REWORK_FACT,
    )
    rework_second = drive_rework_loop(
        WorkflowState.REVIEW_IN_PROGRESS,
        review_fact=REWORK_FACT,
    )
    assert rework_first == rework_second

    held_first = drive_rework_loop(WorkflowState.REVIEW_IN_PROGRESS)
    held_second = drive_rework_loop(WorkflowState.REVIEW_IN_PROGRESS)
    assert held_first == held_second


def test_all_three_drivers_perform_no_io(monkeypatch: pytest.MonkeyPatch) -> None:
    # Purity: block the usual I/O entry points — a pure driver must not notice.
    import builtins
    import socket
    import time

    def _forbidden(call: str) -> Any:
        def _raise(*args: object, **kwargs: object) -> Any:
            raise AssertionError(f"{call} called")

        return _raise

    monkeypatch.setattr(builtins, "open", _forbidden("open"))
    monkeypatch.setattr(socket.socket, "connect", _forbidden("socket.connect"))
    monkeypatch.setattr(time, "time", _forbidden("time.time"))

    assert (
        drive_coding_stage(
            WorkflowState.READY_FOR_CODING,
            coding_fact=STARTED_CODING_FACT,
        )[0]
        is WorkflowState.CODING_IN_PROGRESS
    )
    assert (
        drive_review_stage(
            WorkflowState.CODING_IN_PROGRESS,
            pr_fact=PR_FACT,
        )[0]
        is WorkflowState.REVIEW_IN_PROGRESS
    )
    assert (
        drive_rework_loop(
            WorkflowState.REVIEW_IN_PROGRESS,
            review_fact=REWORK_FACT,
        )[0]
        is WorkflowState.CODING_IN_PROGRESS
    )


def test_bus_is_not_touched_by_the_drivers() -> None:
    # The bus stays an injected seam owned by WorkflowTransitionPublisher; the
    # pure driver module must not reference messaging machinery at all.
    import orchestrator.domain.workflow.transitions as transitions_module

    source = inspect.getsource(transitions_module)
    assert "sfp_messaging" not in source
    assert "MessageBus" not in source
    assert "publish" not in source


def test_drivers_satisfy_the_transition_driver_seam_shape() -> None:
    # The SFP-137 TransitionDriver protocol is `drive(current_state,
    # business_facts) -> WorkflowState`; these drivers' public surface drives
    # the same decision path (fact-gated move through the engine), so a later
    # adapter can wrap them without changing behavior.
    from orchestrator.domain.workflow.state_machine import TransitionDriver

    class CodingStageAdapter:
        """Minimal adapter proving the coding driver fits the SFP-137 seam."""

        def drive(
            self,
            current_state: WorkflowState,
            business_facts: list[str],
        ) -> WorkflowState:
            started = any(
                coding_job_started_fact(job_status=fact.split(":")[-1]) for fact in business_facts
            )
            return drive_coding_stage(
                current_state,
                coding_fact=STARTED_CODING_FACT if started else None,
            )[0]

    driver: TransitionDriver = CodingStageAdapter()  # type: ignore[assignment]
    assert (
        driver.drive(WorkflowState.READY_FOR_CODING, ["coding-job-fact:running"])
        is WorkflowState.CODING_IN_PROGRESS
    )
    assert driver.drive(WorkflowState.READY_FOR_CODING, []) is WorkflowState.READY_FOR_CODING


def test_full_stage_chain_spec_to_review_via_the_drivers() -> None:
    # End-to-end over the landed driver set: spec → coding → review → rework →
    # review, exactly the stage chain SFP-138 + SFP-139 own.
    from orchestrator.domain.workflow.transitions import drive_spec_stage

    state: WorkflowState = WorkflowState.READY_FOR_PR_SPECIFICATION
    state, d1 = drive_spec_stage(
        state,
        plan_fact=("planner", "SUCCESS", ("SFP-139-1",)),
    )
    assert state is WorkflowState.READY_FOR_CODING

    state, d2 = drive_coding_stage(state, coding_fact="running")
    assert state is WorkflowState.CODING_IN_PROGRESS
    assert d2.resulting_state is WorkflowState.CODING_IN_PROGRESS

    state, d3 = drive_review_stage(
        state,
        pr_fact=("coder", "sfp-sfp-139-coding-review"),
    )
    assert state is WorkflowState.REVIEW_IN_PROGRESS
    assert d3.resulting_state is WorkflowState.REVIEW_IN_PROGRESS

    state, d4 = drive_rework_loop(state, review_fact=(7, "CHANGES_REQUESTED"))
    assert state is WorkflowState.CODING_IN_PROGRESS
    assert d4.resulting_state is not WorkflowState.FAILED

    state, d5 = drive_review_stage(
        state,
        pr_fact=("coder", "sfp-sfp-139-coding-review-2"),
    )
    assert state is WorkflowState.REVIEW_IN_PROGRESS

    # Every decision in the chain is a frozen §8.5 record with string states.
    for decision in (d1, d2, d3, d4, d5):
        assert isinstance(decision, WorkflowDecision)
        assert decision.previous_state_name
        assert decision.resulting_state_name
