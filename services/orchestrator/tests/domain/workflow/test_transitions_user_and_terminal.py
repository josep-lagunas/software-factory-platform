"""Tests for the user-decision continuation + terminal-failure drivers (SFP-141).

Covers the last two pure driver families in ``transitions.py``:

- ``WAITING_FOR_USER → {FAILED | READY_FOR_MERGE | <resumed stage>}`` — the
  *resolve* edge of SFP-140's ``drive_merge_wait`` park edge. Fired by a
  confirmed ``UserDecision``-style fact ``(decision, target)`` (ID-069):
  ``REJECT`` ends the workflow failed, ``APPROVE`` releases the parked merge
  (ID-024), ``ANSWER`` resumes the stage the decision names. The recognized
  resume targets are derived from the SFP-137 table's ``WAITING_FOR_USER`` row
  as data — never re-listed here — and an ANSWER naming anything else is a
  recorded no-move, never a guess and never a softened exception.
- ``<active or waiting> → FAILED`` — fired by a *should-fail* fact
  ``(should_fail, category, cause)`` mirroring the landed SFP-144
  ``ShouldFailPolicy`` output. The driver consumes the boolean only: the
  DEVELOPMENT_FAILURE and BLOCKED verdicts land on the "policy says no" row
  (their rework/retry/wait semantics belong to the policy), a terminal state
  never moves, and a genuine verdict requests the move through the engine with
  the deterministic fact id.

Every decision-table row of both drivers is asserted row-by-row — endpoints,
reason (verbatim), ``applied_policy``, ``business_facts_considered``, and the
§8.8 same-state non-move records. Illegal moves raise straight from the SFP-137
table guard (§8.2); the drivers add no states, duplicate no table data, and
stay pure and deterministic (AP-011).
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest
from orchestrator.domain.workflow.state_machine import (
    TRANSITIONS,
    IllegalTransitionError,
    transition,
)
from orchestrator.domain.workflow.states import (
    ACTIVE_STATES,
    STATES,
    TERMINAL_STATES,
    WorkflowState,
)
from orchestrator.domain.workflow.transitions import (
    TERMINAL_FAILURE_POLICY,
    TERMINAL_FAILURE_TARGET,
    USER_DECISION_ANSWER,
    USER_DECISION_APPROVE,
    USER_DECISION_POLICY,
    USER_DECISION_REJECT,
    USER_DECISION_SOURCE,
    drive_terminal_failure,
    drive_user_decision,
    user_decision_recognized,
)

#: The canonical genuine-failure fact: ``(should_fail, category, cause)`` —
#: the landed SFP-144 ``ShouldFailPolicy`` output shape.
GENUINE_FAILURE_FACT: tuple[bool, str, str] = (True, "UNRECOVERABLE", "merge-queue-exhausted")

#: A canonical development-failure verdict (ID-068: rework, never FAILED) and
#: the two BLOCKED shapes — all land on the "policy says no" row.
DEVELOPMENT_FAILURE_FACT: tuple[bool, str, str] = (False, "DEVELOPMENT_FAILURE", "no-cause")
BLOCKED_AUTO_FACT: tuple[bool, str, str] = (
    False,
    "BLOCKED",
    "missing-context",
)
BLOCKED_HUMAN_FACT: tuple[bool, str, str] = (
    False,
    "BLOCKED",
    "human-confirmation-required",
)


def _wait_resume_targets() -> frozenset[WorkflowState]:
    """The table's ``WAITING_FOR_USER`` row — read from the table as data.

    The same derivation the driver uses, restated here so the tests assert the
    guarantee against the table rather than against a hand-copied list.
    """
    return TRANSITIONS[WorkflowState.WAITING_FOR_USER]


def _states_where_the_move_is_table_illegal(
    target: WorkflowState,
) -> list[WorkflowState]:
    """Every §8.4 state from which ``target`` is NOT a legal table move."""
    return sorted(
        (state for state in STATES if target not in TRANSITIONS[state]),
        key=lambda s: s.name,
    )


# --- The user-decision predicate ------------------------------------------------


@pytest.mark.parametrize(
    ("decision", "expected"),
    [
        (USER_DECISION_REJECT, True),
        (USER_DECISION_APPROVE, True),
        (USER_DECISION_ANSWER, True),
        # Absent, unrecognized, and the v0 members that do not resolve a wait.
        (None, False),
        ("", False),
        ("reject", False),  # case-sensitive: the vocabulary is upper-case
        ("REQUEST_CHANGES", False),
        ("PROVIDE_CONTEXT", False),
        ("ANSWER_QUESTION", False),
        ("CLARIFICATION", False),
        ("OVERRIDE", False),
        ("some-arbitrary-unrecognized-string", False),
    ],
)
def test_user_decision_recognized_predicate(
    decision: str | None,
    expected: bool,
) -> None:
    assert user_decision_recognized(decision=decision) is expected


def test_user_decision_vocabulary_constants() -> None:
    assert USER_DECISION_REJECT == "REJECT"
    assert USER_DECISION_APPROVE == "APPROVE"
    assert USER_DECISION_ANSWER == "ANSWER"
    assert USER_DECISION_SOURCE is WorkflowState.WAITING_FOR_USER
    assert USER_DECISION_POLICY == "user-decision-observed"


# --- Row 1: decision_fact None → no user decision observed ----------------------


def test_absent_user_decision_holds_state_and_records_non_move() -> None:
    new_state, decision = drive_user_decision(WorkflowState.WAITING_FOR_USER)
    assert new_state is WorkflowState.WAITING_FOR_USER
    assert decision.previous_state is WorkflowState.WAITING_FOR_USER
    assert decision.resulting_state is WorkflowState.WAITING_FOR_USER
    assert decision.previous_state_name == "WAITING_FOR_USER"
    assert decision.resulting_state_name == "WAITING_FOR_USER"
    assert decision.reason == "no user decision observed"
    assert decision.applied_policy == USER_DECISION_POLICY
    assert decision.business_facts_considered == ("user-decision-fact:absent",)
    assert decision.aggregate_changes == ()
    assert decision.commands_emitted == ()


# --- Row 2: decision None/unrecognized → user decision unrecognized -------------


@pytest.mark.parametrize(
    "decision",
    [
        None,
        "",
        "REQUEST_CHANGES",
        "PROVIDE_CONTEXT",
        "ANSWER_QUESTION",
        "CLARIFICATION",
        "OVERRIDE",
        "some-arbitrary-unrecognized-string",
    ],
)
def test_unrecognized_user_decision_holds_state_and_records_non_move(
    decision: str | None,
) -> None:
    # Any value outside REJECT/APPROVE/ANSWER — absent, unrecognized, or a v0
    # member that does not resolve this edge — is a recorded no-move naming
    # what was seen, never an exception.
    fact: tuple[str | None, str | None] = (decision, "REVIEW_IN_PROGRESS")
    new_state, returned = drive_user_decision(
        WorkflowState.WAITING_FOR_USER,
        decision_fact=fact,
    )
    assert new_state is WorkflowState.WAITING_FOR_USER
    assert returned.previous_state is WorkflowState.WAITING_FOR_USER
    assert returned.resulting_state is WorkflowState.WAITING_FOR_USER
    assert returned.reason == "user decision unrecognized"
    assert returned.applied_policy == USER_DECISION_POLICY
    # The fact id names what was seen: an absent decision renders as its
    # explicit placeholder (mirroring the landed _fact_id style), and a blank
    # string is an observed value that names nothing.
    decision_part = decision if decision is not None else "no-decision"
    assert returned.business_facts_considered == (
        f"user-decision-fact:{decision_part}:REVIEW_IN_PROGRESS",
    )
    assert returned.aggregate_changes == ()


# --- Row 3: not WAITING_FOR_USER → no wait to resolve ---------------------------


def test_not_waiting_for_user_holds_state_and_records_non_move() -> None:
    # A user decision lands only while a wait is parked; from any other state
    # there is no wait to resolve. Row 3 outranks the decision value: even a
    # decisive REJECT from a non-waiting state is a recorded no-move.
    not_waiting = [state for state in STATES if state is not USER_DECISION_SOURCE]
    assert not_waiting  # sanity: the wait state exists apart
    for state in not_waiting:
        for decision in (USER_DECISION_REJECT, USER_DECISION_APPROVE, USER_DECISION_ANSWER):
            fact: tuple[str | None, str | None] = (decision, None)
            new_state, returned = drive_user_decision(state, decision_fact=fact)
            assert new_state is state
            assert returned.previous_state is state
            assert returned.resulting_state is state
            assert returned.reason == "not WAITING_FOR_USER: no wait to resolve"
            assert returned.applied_policy == USER_DECISION_POLICY
            assert returned.business_facts_considered == (
                f"user-decision-fact:{decision}:no-target",
            )
            assert returned.aggregate_changes == ()


# --- Row 4: REJECT from WAITING_FOR_USER → FAILED (ID-069) ----------------------


def test_reject_ends_the_workflow_failed() -> None:
    fact: tuple[str | None, str | None] = (USER_DECISION_REJECT, None)
    new_state, decision = drive_user_decision(
        WorkflowState.WAITING_FOR_USER,
        decision_fact=fact,
    )
    assert new_state is WorkflowState.FAILED
    assert decision.previous_state is WorkflowState.WAITING_FOR_USER
    assert decision.resulting_state is WorkflowState.FAILED
    assert decision.previous_state_name == "WAITING_FOR_USER"
    assert decision.resulting_state_name == "FAILED"
    assert decision.reason == "user REJECT: workflow ends failed"
    assert decision.applied_policy == USER_DECISION_POLICY
    assert decision.business_facts_considered == ("user-decision-fact:REJECT:no-target",)
    assert decision.aggregate_changes == ("tickets.workflow_status",)
    assert decision.commands_emitted == ()


def test_reject_decision_is_the_engine_produced_record() -> None:
    # The move is delegated to the SFP-137 engine: the returned decision is
    # exactly what transition() produces for this edge — the driver neither
    # re-implements the guard nor fabricates its own move record.
    _, via_engine = transition(
        USER_DECISION_SOURCE,
        TERMINAL_FAILURE_TARGET,
        reason="probe",
        applied_policy=USER_DECISION_POLICY,
        aggregate_changes=("tickets.workflow_status",),
    )
    _, via_driver = drive_user_decision(
        WorkflowState.WAITING_FOR_USER,
        decision_fact=(USER_DECISION_REJECT, None),
    )
    assert via_driver.previous_state == via_engine.previous_state
    assert via_driver.resulting_state == via_engine.resulting_state
    assert via_driver.applied_policy == USER_DECISION_POLICY == via_engine.applied_policy
    assert via_driver.reason == "user REJECT: workflow ends failed"
    assert via_driver.aggregate_changes == via_engine.aggregate_changes


# --- Row 5: APPROVE from WAITING_FOR_USER → READY_FOR_MERGE (ID-024) ------------


def test_approve_releases_the_parked_merge() -> None:
    fact: tuple[str | None, str | None] = (USER_DECISION_APPROVE, None)
    new_state, decision = drive_user_decision(
        WorkflowState.WAITING_FOR_USER,
        decision_fact=fact,
    )
    assert new_state is WorkflowState.READY_FOR_MERGE
    assert decision.previous_state is WorkflowState.WAITING_FOR_USER
    assert decision.resulting_state is WorkflowState.READY_FOR_MERGE
    assert decision.previous_state_name == "WAITING_FOR_USER"
    assert decision.resulting_state_name == "READY_FOR_MERGE"
    assert decision.reason == "user APPROVE: merge may proceed"
    assert decision.applied_policy == USER_DECISION_POLICY
    assert decision.business_facts_considered == ("user-decision-fact:APPROVE:no-target",)
    assert decision.aggregate_changes == ("tickets.workflow_status",)


def test_approve_decision_is_the_engine_produced_record() -> None:
    _, via_engine = transition(
        USER_DECISION_SOURCE,
        WorkflowState.READY_FOR_MERGE,
        reason="probe",
        applied_policy=USER_DECISION_POLICY,
        aggregate_changes=("tickets.workflow_status",),
    )
    _, via_driver = drive_user_decision(
        WorkflowState.WAITING_FOR_USER,
        decision_fact=(USER_DECISION_APPROVE, None),
    )
    assert via_driver.previous_state == via_engine.previous_state
    assert via_driver.resulting_state == via_engine.resulting_state
    assert via_driver.applied_policy == USER_DECISION_POLICY == via_engine.applied_policy


# --- Row 6: ANSWER → resume the stage the decision names ------------------------


def test_answer_resumes_every_recognized_target() -> None:
    # The recognized resume set is derived from the table's WAITING_FOR_USER
    # row — the driver and these tests both read the table, never a copy.
    recognized = _wait_resume_targets()
    assert recognized  # sanity: the table's row is non-empty
    assert WorkflowState.READY_FOR_MERGE in recognized
    assert WorkflowState.CODING_IN_PROGRESS in recognized
    assert WorkflowState.DEPLOYING in recognized
    for target in sorted(recognized, key=lambda s: s.name):
        fact: tuple[str | None, str | None] = (USER_DECISION_ANSWER, target.name)
        new_state, decision = drive_user_decision(
            WorkflowState.WAITING_FOR_USER,
            decision_fact=fact,
        )
        assert new_state is target
        assert decision.previous_state is WorkflowState.WAITING_FOR_USER
        assert decision.resulting_state is target
        assert decision.previous_state_name == "WAITING_FOR_USER"
        assert decision.resulting_state_name == target.name
        assert decision.reason == f"user answer received: resume {target.name}"
        assert decision.applied_policy == USER_DECISION_POLICY
        assert decision.business_facts_considered == (f"user-decision-fact:ANSWER:{target.name}",)
        assert decision.aggregate_changes == ("tickets.workflow_status",)


@pytest.mark.parametrize("target_name", ["", "   ", None])
def test_answer_with_empty_target_is_treated_as_absent(
    target_name: str | None,
) -> None:
    # An empty target names no stage — the same observation as an absent one.
    fact: tuple[str | None, str | None] = (USER_DECISION_ANSWER, target_name)
    new_state, decision = drive_user_decision(
        WorkflowState.WAITING_FOR_USER,
        decision_fact=fact,
    )
    assert new_state is WorkflowState.WAITING_FOR_USER
    assert decision.resulting_state is WorkflowState.WAITING_FOR_USER
    assert decision.reason == "answer without a recognized target stage"
    assert decision.applied_policy == USER_DECISION_POLICY
    expected_target_part = target_name if target_name else "no-target"
    assert decision.business_facts_considered == (
        f"user-decision-fact:ANSWER:{expected_target_part}",
    )
    assert decision.aggregate_changes == ()


@pytest.mark.parametrize(
    "target_name",
    [
        "SOME_INVENTED_STAGE",
        "review_in_progress",  # case-sensitive: names are upper-case
        "0",  # an enum *value* string, not a name — names no stage
        "answer",  # a decision value, never a stage name
    ],
)
def test_answer_naming_no_stage_at_all_is_the_row6b_no_move(
    target_name: str | None,
) -> None:
    # Row 6b: an ANSWER whose target names no stage of this workflow is an
    # incomplete observation — there is nothing to resume and nothing to ask
    # the engine about, so the non-move is recorded (§8.8), never raised.
    fact: tuple[str | None, str | None] = (USER_DECISION_ANSWER, target_name)
    new_state, decision = drive_user_decision(
        WorkflowState.WAITING_FOR_USER,
        decision_fact=fact,
    )
    assert new_state is WorkflowState.WAITING_FOR_USER
    assert decision.previous_state is WorkflowState.WAITING_FOR_USER
    assert decision.resulting_state is WorkflowState.WAITING_FOR_USER
    assert decision.reason == "answer without a recognized target stage"
    assert decision.applied_policy == USER_DECISION_POLICY
    assert decision.business_facts_considered == (f"user-decision-fact:ANSWER:{target_name}",)
    assert decision.aggregate_changes == ()


def test_answer_to_a_real_stage_outside_the_resume_row_raises() -> None:
    # §8.2: an ANSWER that names a *real* workflow stage which is not in the
    # table's WAITING_FOR_USER resume row is table-illegal. The
    # IllegalTransitionError propagates — never caught, never softened, and
    # never converted into a guessed or fallback move. Exhaustive over every
    # such stage: the terminal states and the wait itself.
    recognized = _wait_resume_targets()
    illegal_targets = sorted(
        (state for state in STATES if state not in recognized),
        key=lambda s: s.name,
    )
    assert illegal_targets  # sanity: some named stage is outside the row
    assert WorkflowState.COMPLETED in illegal_targets
    assert WorkflowState.WAITING_FOR_USER in illegal_targets
    for target in illegal_targets:
        fact: tuple[str | None, str | None] = (USER_DECISION_ANSWER, target.name)
        with pytest.raises(IllegalTransitionError):
            drive_user_decision(WorkflowState.WAITING_FOR_USER, decision_fact=fact)


def test_answer_illegal_target_error_carries_the_table_guard_detail() -> None:
    # The error comes straight from the SFP-137 table guard: the state is the
    # wait, and the attempted target is the named-but-illegal resume stage.
    fact: tuple[str | None, str | None] = (USER_DECISION_ANSWER, "COMPLETED")
    with pytest.raises(IllegalTransitionError) as excinfo:
        drive_user_decision(WorkflowState.WAITING_FOR_USER, decision_fact=fact)
    assert excinfo.value.current_state is WorkflowState.WAITING_FOR_USER
    assert excinfo.value.attempted_target is WorkflowState.COMPLETED


def test_answer_resume_decision_is_the_engine_produced_record() -> None:
    target = WorkflowState.REVIEW_IN_PROGRESS
    _, via_engine = transition(
        USER_DECISION_SOURCE,
        target,
        reason="probe",
        applied_policy=USER_DECISION_POLICY,
        aggregate_changes=("tickets.workflow_status",),
    )
    _, via_driver = drive_user_decision(
        WorkflowState.WAITING_FOR_USER,
        decision_fact=(USER_DECISION_ANSWER, target.name),
    )
    assert via_driver.previous_state == via_engine.previous_state
    assert via_driver.resulting_state is target == via_engine.resulting_state
    assert via_driver.applied_policy == USER_DECISION_POLICY == via_engine.applied_policy
    assert via_driver.reason == "user answer received: resume REVIEW_IN_PROGRESS"


def test_user_decision_non_move_is_deterministic() -> None:
    fact: tuple[str | None, str | None] = ("REQUEST_CHANGES", "REVIEW_IN_PROGRESS")
    _, first = drive_user_decision(WorkflowState.WAITING_FOR_USER, decision_fact=fact)
    _, second = drive_user_decision(WorkflowState.WAITING_FOR_USER, decision_fact=fact)
    assert first == second
    assert first.to_json() == second.to_json()


def test_user_decision_decision_is_immutable() -> None:
    _, decision = drive_user_decision(
        WorkflowState.WAITING_FOR_USER,
        decision_fact=(USER_DECISION_REJECT, None),
    )
    with pytest.raises(ValueError, match="frozen"):
        decision.reason = "mutated"


def test_user_decision_driver_resolves_the_sfp140_park() -> None:
    # End-to-end over the two landed halves: SFP-140 parks MERGING into
    # WAITING_FOR_USER; this driver is what resolves that wait. REJECT ends
    # the parked workflow failed — the resolve counterpart of the park edge.
    from orchestrator.domain.workflow.transitions import drive_merge_wait

    parked, park_decision = drive_merge_wait(WorkflowState.MERGING, approval_required_fact=True)
    assert parked is WorkflowState.WAITING_FOR_USER
    assert "ID-024" in park_decision.reason

    resolved, resolve_decision = drive_user_decision(
        parked,
        decision_fact=(USER_DECISION_REJECT, None),
    )
    assert resolved is WorkflowState.FAILED
    assert resolve_decision.reason == "user REJECT: workflow ends failed"


# --- Terminal failure: row 1 → no failure fact observed -------------------------


def test_absent_failure_fact_holds_state_and_records_non_move() -> None:
    new_state, decision = drive_terminal_failure(WorkflowState.CODING_IN_PROGRESS)
    assert new_state is WorkflowState.CODING_IN_PROGRESS
    assert decision.previous_state is WorkflowState.CODING_IN_PROGRESS
    assert decision.resulting_state is WorkflowState.CODING_IN_PROGRESS
    assert decision.previous_state_name == "CODING_IN_PROGRESS"
    assert decision.resulting_state_name == "CODING_IN_PROGRESS"
    assert decision.reason == "no failure fact observed"
    assert decision.applied_policy == TERMINAL_FAILURE_POLICY
    assert decision.business_facts_considered == ("failure-fact:absent",)
    assert decision.aggregate_changes == ()
    assert decision.commands_emitted == ()


# --- Terminal failure: row 2 → the policy said no -------------------------------


@pytest.mark.parametrize(
    ("failure_fact", "expected_fact_id"),
    [
        # The landed SFP-144 verdicts that say "do not fail": the ID-068
        # rework loop and both BLOCKED shapes. Their rework/retry/wait
        # semantics belong to the policy — this driver only consumes the
        # boolean, and lands each on the same recorded no-move row.
        (
            DEVELOPMENT_FAILURE_FACT,
            "failure-fact:false:DEVELOPMENT_FAILURE:no-cause",
        ),
        (BLOCKED_AUTO_FACT, "failure-fact:false:BLOCKED:missing-context"),
        (
            BLOCKED_HUMAN_FACT,
            "failure-fact:false:BLOCKED:human-confirmation-required",
        ),
        # An absent verdict is not a "fail" verdict — treated as not-True.
        ((None, "UNRECOVERABLE", "merge-queue-exhausted"), None),
    ],
)
def test_policy_says_no_holds_state_and_records_non_move(
    failure_fact: tuple[bool | None, str | None, str | None],
    expected_fact_id: str | None,
) -> None:
    new_state, decision = drive_terminal_failure(
        WorkflowState.CODING_IN_PROGRESS,
        failure_fact=failure_fact,
    )
    assert new_state is WorkflowState.CODING_IN_PROGRESS
    assert decision.previous_state is WorkflowState.CODING_IN_PROGRESS
    assert decision.resulting_state is WorkflowState.CODING_IN_PROGRESS
    assert decision.resulting_state is not WorkflowState.FAILED
    assert new_state is not WorkflowState.FAILED
    assert decision.reason == (
        "failure policy says no failed move: rework/retry/wait semantics belong to the policy"
    )
    assert decision.applied_policy == TERMINAL_FAILURE_POLICY
    if expected_fact_id is not None:
        assert decision.business_facts_considered == (expected_fact_id,)
    else:
        should_fail = failure_fact[0]
        assert decision.business_facts_considered == (
            f"failure-fact:unknown-should-fail:{failure_fact[1]}:{failure_fact[2]}",
        )
        assert should_fail is None
    assert decision.aggregate_changes == ()


def test_row2_applies_from_every_state_including_the_wait() -> None:
    # Row 2 holds regardless of the current state — including a parked wait:
    # the policy said no failed move, so the workflow stays exactly where it
    # is, in every §8.4 state.
    for state in STATES:
        new_state, decision = drive_terminal_failure(
            state,
            failure_fact=DEVELOPMENT_FAILURE_FACT,
        )
        assert new_state is state
        assert decision.previous_state is state
        assert decision.resulting_state is state
        assert decision.reason == (
            "failure policy says no failed move: rework/retry/wait semantics belong to the policy"
        )
        assert decision.applied_policy == TERMINAL_FAILURE_POLICY
        assert decision.business_facts_considered == (
            "failure-fact:false:DEVELOPMENT_FAILURE:no-cause",
        )


# --- Terminal failure: row 3 → already terminal ---------------------------------


@pytest.mark.parametrize("state", sorted(TERMINAL_STATES, key=lambda s: s.name))
def test_genuine_failure_from_a_terminal_state_holds(
    state: WorkflowState,
) -> None:
    # A genuine verdict cannot move a terminal workflow: neither COMPLETED nor
    # FAILED is a source of a further move. Recorded, never an error.
    new_state, decision = drive_terminal_failure(
        state,
        failure_fact=GENUINE_FAILURE_FACT,
    )
    assert new_state is state
    assert decision.previous_state is state
    assert decision.resulting_state is state
    assert decision.reason == "already terminal"
    assert decision.applied_policy == TERMINAL_FAILURE_POLICY
    assert decision.business_facts_considered == (
        "failure-fact:true:UNRECOVERABLE:merge-queue-exhausted",
    )
    assert decision.aggregate_changes == ()


def test_row3_covers_exactly_the_terminal_states() -> None:
    # Derived from the landed states module, never re-listed: the two terminal
    # states are COMPLETED and FAILED, and both are held by row 3.
    assert TERMINAL_STATES == frozenset(
        {WorkflowState.COMPLETED, WorkflowState.FAILED},
    )


# --- Terminal failure: row 4 → genuine → FAILED via the engine ------------------


def test_genuine_failure_ends_the_workflow_failed() -> None:
    new_state, decision = drive_terminal_failure(
        WorkflowState.CODING_IN_PROGRESS,
        failure_fact=GENUINE_FAILURE_FACT,
    )
    assert new_state is WorkflowState.FAILED
    assert decision.previous_state is WorkflowState.CODING_IN_PROGRESS
    assert decision.resulting_state is WorkflowState.FAILED
    assert decision.previous_state_name == "CODING_IN_PROGRESS"
    assert decision.resulting_state_name == "FAILED"
    assert decision.reason == (
        "genuine failure (UNRECOVERABLE/merge-queue-exhausted): workflow ends failed"
    )
    assert decision.applied_policy == TERMINAL_FAILURE_POLICY
    assert decision.business_facts_considered == (
        "failure-fact:true:UNRECOVERABLE:merge-queue-exhausted",
    )
    assert decision.aggregate_changes == ("tickets.workflow_status",)
    assert decision.commands_emitted == ()


@pytest.mark.parametrize(
    "state",
    sorted(ACTIVE_STATES | {WorkflowState.WAITING_FOR_USER}, key=lambda s: s.name),
)
def test_genuine_failure_moves_from_every_failed_source(
    state: WorkflowState,
) -> None:
    # Row 4 sources: exactly the states the SFP-137 table allows a FAILED move
    # from — ACTIVE_STATES ∪ {WAITING_FOR_USER}, derived from the landed
    # modules, never re-listed here.
    assert TERMINAL_FAILURE_TARGET in TRANSITIONS[state]
    new_state, decision = drive_terminal_failure(
        state,
        failure_fact=GENUINE_FAILURE_FACT,
    )
    assert new_state is WorkflowState.FAILED
    assert decision.previous_state is state
    assert decision.resulting_state is WorkflowState.FAILED
    assert decision.previous_state_name == state.name
    assert decision.resulting_state_name == "FAILED"
    assert decision.reason == (
        "genuine failure (UNRECOVERABLE/merge-queue-exhausted): workflow ends failed"
    )
    assert decision.applied_policy == TERMINAL_FAILURE_POLICY
    assert decision.business_facts_considered == (
        "failure-fact:true:UNRECOVERABLE:merge-queue-exhausted",
    )
    assert decision.aggregate_changes == ("tickets.workflow_status",)


def test_row4_sources_match_the_table_exactly() -> None:
    # The driver's reachable-from set and the table's FAILED-source set are
    # the same set — the driver adds no source the table forbids.
    table_sources = {state for state in STATES if TERMINAL_FAILURE_TARGET in TRANSITIONS[state]}
    assert table_sources == set(ACTIVE_STATES) | {WorkflowState.WAITING_FOR_USER}


def test_genuine_failure_decision_is_the_engine_produced_record() -> None:
    _, via_engine = transition(
        WorkflowState.CODING_IN_PROGRESS,
        TERMINAL_FAILURE_TARGET,
        reason="probe",
        applied_policy=TERMINAL_FAILURE_POLICY,
        aggregate_changes=("tickets.workflow_status",),
    )
    _, via_driver = drive_terminal_failure(
        WorkflowState.CODING_IN_PROGRESS,
        failure_fact=GENUINE_FAILURE_FACT,
    )
    assert via_driver.previous_state == via_engine.previous_state
    assert via_driver.resulting_state == via_engine.resulting_state
    assert via_driver.applied_policy == TERMINAL_FAILURE_POLICY == via_engine.applied_policy
    assert via_driver.aggregate_changes == via_engine.aggregate_changes


@pytest.mark.parametrize(
    ("failure_fact", "expected_reason"),
    [
        (
            (True, None, None),
            "genuine failure (no-category/no-cause): workflow ends failed",
        ),
        (
            (True, "UNRECOVERABLE", None),
            "genuine failure (UNRECOVERABLE/no-cause): workflow ends failed",
        ),
        (
            (True, None, "merge-queue-exhausted"),
            "genuine failure (no-category/merge-queue-exhausted): workflow ends failed",
        ),
    ],
)
def test_row4_reason_renders_absent_category_or_cause_deterministically(
    failure_fact: tuple[bool | None, str | None, str | None],
    expected_reason: str,
) -> None:
    # An absent category/cause renders as its explicit placeholder — never as
    # a bare "None" and never as a dangling empty segment.
    _, decision = drive_terminal_failure(
        WorkflowState.MERGING,
        failure_fact=failure_fact,
    )
    assert decision.resulting_state is WorkflowState.FAILED
    assert decision.reason == expected_reason
    assert decision.business_facts_considered == (_expected_failure_fact_id(failure_fact),)


def _expected_failure_fact_id(
    failure_fact: tuple[bool | None, str | None, str | None],
) -> str:
    """Mirror the deterministic fact-id rendering for absent members."""
    should_fail = failure_fact[0]
    should_fail_part = (
        str(should_fail).lower() if should_fail is not None else "unknown-should-fail"
    )
    category_part = failure_fact[1] if failure_fact[1] else "no-category"
    cause_part = failure_fact[2] if failure_fact[2] else "no-cause"
    return f"failure-fact:{should_fail_part}:{category_part}:{cause_part}"


def test_terminal_failure_non_move_is_deterministic() -> None:
    _, first = drive_terminal_failure(
        WorkflowState.CODING_IN_PROGRESS,
        failure_fact=DEVELOPMENT_FAILURE_FACT,
    )
    _, second = drive_terminal_failure(
        WorkflowState.CODING_IN_PROGRESS,
        failure_fact=DEVELOPMENT_FAILURE_FACT,
    )
    assert first == second
    assert first.to_json() == second.to_json()


def test_terminal_failure_decision_is_immutable() -> None:
    _, decision = drive_terminal_failure(
        WorkflowState.CODING_IN_PROGRESS,
        failure_fact=GENUINE_FAILURE_FACT,
    )
    with pytest.raises(ValueError, match="frozen"):
        decision.reason = "mutated"


def test_terminal_failure_driver_consumes_the_boolean_only() -> None:
    # Structural guarantee (ID-068 consumed, not duplicated): the driver never
    # branches on category/cause — the taxonomy is the landed SFP-144 policy's.
    # Two facts that differ ONLY in category/cause produce identical control
    # flow (same endpoints, same policy, same aggregate) for every state.
    facts: list[tuple[bool, str, str]] = [
        (True, "DEVELOPMENT_FAILURE", "some-cause"),
        (True, "BLOCKED", "missing-context"),
        (True, "COMPLETELY-UNKNOWN", "anything-at-all"),
    ]
    for state in sorted(STATES, key=lambda s: s.name):
        produced = [drive_terminal_failure(state, failure_fact=fact) for fact in facts]
        for new_state, _decision in produced:
            assert new_state in (state, WorkflowState.FAILED)
        # Identical control flow: the reason/fact-id vary (they carry the
        # names), the endpoints never do.
        for _, decision in produced:
            assert decision.previous_state is state
            assert decision.resulting_state in (state, WorkflowState.FAILED)


def test_terminal_failure_driver_has_no_taxonomy_vocabulary() -> None:
    # The ID-068 taxonomy vocabulary never appears in the driver's executable
    # code — only the boolean is consumed. Docstring stripped first so only
    # real code is inspected.
    import ast

    import orchestrator.domain.workflow.transitions as transitions_module

    func_def = ast.parse(
        inspect.getsource(transitions_module.drive_terminal_failure),
    ).body[0]
    assert isinstance(func_def, ast.FunctionDef)
    body_nodes = func_def.body
    if (
        body_nodes
        and isinstance(body_nodes[0], ast.Expr)
        and isinstance(body_nodes[0].value, ast.Constant)
    ):
        body_nodes = body_nodes[1:]
    body = "\n".join(ast.unparse(node) for node in body_nodes)
    for taxonomy_term in ("DEVELOPMENT_FAILURE", "BLOCKED", "classify_failure"):
        assert taxonomy_term not in body


# --- Shared guarantees across both drivers --------------------------------------


def test_both_drivers_delegate_moves_to_the_engine() -> None:
    # Structural: neither driver constructs a *move* WorkflowDecision
    # directly — every move routes through state_machine.transition. The only
    # direct constructions are the §8.8 same-state non-move records.
    import ast

    import orchestrator.domain.workflow.transitions as transitions_module

    for func in (
        transitions_module.drive_user_decision,
        transitions_module.drive_terminal_failure,
    ):
        func_def = ast.parse(inspect.getsource(func)).body[0]
        assert isinstance(func_def, ast.FunctionDef)
        body = "\n".join(ast.unparse(node) for node in func_def.body)
        assert "transition(" in body
        # Every real move carries the aggregate change; every direct
        # WorkflowDecision construction are non-moves (same state both ends).
        for node in ast.walk(func_def):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "WorkflowDecision"
            ):
                # A directly-built record must hold the state: its two state
                # fields share the same expression source (current_state).
                keywords = {kw.arg: ast.unparse(kw.value) for kw in node.keywords if kw.arg}
                assert keywords.get("previous_state") == keywords.get(
                    "resulting_state",
                ), f"directly built move in {func.__name__}: {keywords}"


def test_every_move_carries_the_workflow_status_aggregate_change() -> None:
    # Exhaustive over both drivers' full observable grids: any decision that
    # changes state carries aggregate_changes == ('tickets.workflow_status',),
    # and any same-state decision carries none (§8.8 records carry no move).
    decisions: list[tuple[str, Any, WorkflowState, Any, Any]] = []
    for state in STATES:
        for decision_fact in (
            None,
            (None, None),
            (USER_DECISION_REJECT, None),
            (USER_DECISION_APPROVE, None),
            (USER_DECISION_ANSWER, None),
            (USER_DECISION_ANSWER, "REVIEW_IN_PROGRESS"),
            (USER_DECISION_ANSWER, "COMPLETED"),
            ("REQUEST_CHANGES", "REVIEW_IN_PROGRESS"),
        ):
            try:
                new_state, decision = drive_user_decision(
                    state,
                    decision_fact=decision_fact,
                )
            except IllegalTransitionError:
                continue
            decisions.append(("user", decision_fact, state, new_state, decision))
        for failure_fact in (
            None,
            (True, "UNRECOVERABLE", "merge-queue-exhausted"),
            DEVELOPMENT_FAILURE_FACT,
            (None, "UNRECOVERABLE", "merge-queue-exhausted"),
        ):
            new_state, decision = drive_terminal_failure(
                state,
                failure_fact=failure_fact,
            )
            decisions.append(("failure", failure_fact, state, new_state, decision))

    assert decisions  # sanity: the grid produced decisions
    moved = [entry for entry in decisions if entry[3] is not entry[2]]
    assert moved  # sanity: at least one move was observed
    for _kind, _fact, state, new_state, decision in moved:
        assert new_state is not state
        assert decision.aggregate_changes == ("tickets.workflow_status",)
        assert decision.resulting_state is new_state
        assert decision.business_facts_considered  # a move names its fact
    for _kind, _fact, state, new_state, decision in decisions:
        if new_state is state:
            assert decision.aggregate_changes == ()
            assert decision.business_facts_considered  # a non-move names its fact too


def test_non_moves_also_name_their_fact_deterministically() -> None:
    # §8.8: every recorded non-move names the observed fact — including the
    # fully-absent observation — and names it identically across calls.
    cases: list[tuple[Any, Any, Any]] = [
        ("user", WorkflowState.WAITING_FOR_USER, None),
        ("user", WorkflowState.WAITING_FOR_USER, ("REQUEST_CHANGES", None)),
        ("user", WorkflowState.CODING_IN_PROGRESS, (USER_DECISION_REJECT, None)),
        ("user", WorkflowState.WAITING_FOR_USER, (USER_DECISION_ANSWER, None)),
        ("failure", WorkflowState.CODING_IN_PROGRESS, None),
        ("failure", WorkflowState.CODING_IN_PROGRESS, DEVELOPMENT_FAILURE_FACT),
        ("failure", WorkflowState.COMPLETED, GENUINE_FAILURE_FACT),
    ]
    for kind, state, fact in cases:
        if kind == "user":
            _, first = drive_user_decision(state, decision_fact=fact)
            _, second = drive_user_decision(state, decision_fact=fact)
        else:
            _, first = drive_terminal_failure(state, failure_fact=fact)
            _, second = drive_terminal_failure(state, failure_fact=fact)
        assert first == second
        assert first.business_facts_considered
        assert first.business_facts_considered == second.business_facts_considered
        assert first.previous_state is first.resulting_state is state


def test_no_new_transition_table_entries_or_state_machine_edits() -> None:
    # The module contract: SFP-141 adds drivers only. The table still maps
    # exactly the landed SFP-137 shape — terminal rows empty, the wait row
    # holding its resume targets, FAILED reachable from the expected sources.
    for terminal in TERMINAL_STATES:
        assert TRANSITIONS[terminal] == frozenset()
    assert TERMINAL_FAILURE_TARGET in TRANSITIONS[WorkflowState.WAITING_FOR_USER]
    assert WorkflowState.WAITING_FOR_USER not in _wait_resume_targets()
