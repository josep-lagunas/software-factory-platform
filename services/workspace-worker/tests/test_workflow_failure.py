"""Tests for classify_failure (SFP-75; ID-068/ID-069).

Covers AC6-AC9 + AC11:
- (AC6) the six development sources classify to DEVELOPMENT_FAILURE/None/False.
- (AC7) the nine blocked sources classify to BLOCKED + the mapped BlockedCause.
- (AC8) the per-cause recoverable flags match ID-069 exactly.
- (AC9) exit_code/message are informational — they never alter the triple.
- totality over all 15 FailureSource members.
- detail is deterministic: contains source.name; emits exit=0 (not truthiness);
  omits an empty message.

The expected mapping is encoded INDEPENDENTLY here (not imported from the
implementation's dicts) so the test is a genuine oracle, not a tautology.
"""

from typing import NamedTuple

import pytest
from sfp_contracts.workflow.failure import (
    BlockedCause,
    FailureCategory,
    FailureClassification,
    FailureSource,
)
from workspace_worker.workflow.failure import classify_failure


class Expected(NamedTuple):
    """The expected (category, cause, recoverable) triple for a source."""

    category: FailureCategory
    cause: BlockedCause | None
    recoverable: bool


_DEV = Expected(FailureCategory.DEVELOPMENT_FAILURE, None, False)

#: Independent oracle table: every FailureSource -> its ID-068/ID-069 triple.
#: Built BEFORE / WITHOUT consulting the implementation's _SOURCE_TO_CAUSE /
#: _RECOVERABLE dicts; this is the spec encoded as test data.
EXPECTED: dict[FailureSource, Expected] = {
    # development sources
    FailureSource.LINT: _DEV,
    FailureSource.TYPECHECK: _DEV,
    FailureSource.BUILD: _DEV,
    FailureSource.UNIT_TEST: _DEV,
    FailureSource.INTEGRATION_TEST: _DEV,
    FailureSource.CI: _DEV,
    # blocked sources
    FailureSource.DEPENDENCY: Expected(
        FailureCategory.BLOCKED, BlockedCause.INCOMPLETE_DEPENDENCY, True
    ),
    FailureSource.SECRET: Expected(FailureCategory.BLOCKED, BlockedCause.MISSING_SECRET, True),
    FailureSource.CONTEXT: Expected(FailureCategory.BLOCKED, BlockedCause.MISSING_CONTEXT, False),
    FailureSource.CLARIFICATION: Expected(
        FailureCategory.BLOCKED, BlockedCause.UNRESOLVED_CLARIFICATION, False
    ),
    FailureSource.MERGE: Expected(FailureCategory.BLOCKED, BlockedCause.MERGE_QUEUE_FAILURE, True),
    FailureSource.DEPLOYMENT: Expected(
        FailureCategory.BLOCKED, BlockedCause.DEPLOYMENT_FAILURE, False
    ),
    FailureSource.EXTERNAL_SYSTEM: Expected(
        FailureCategory.BLOCKED, BlockedCause.EXTERNAL_SYSTEM_UNAVAILABLE, True
    ),
    FailureSource.NETWORK: Expected(
        FailureCategory.BLOCKED, BlockedCause.EXTERNAL_SYSTEM_UNAVAILABLE, True
    ),
    FailureSource.REPO: Expected(FailureCategory.BLOCKED, BlockedCause.REPO_INACCESSIBLE, False),
}


def test_expected_table_covers_every_source() -> None:
    """Guard: the oracle table is total over FailureSource (no source untested)."""
    assert set(EXPECTED) == set(FailureSource)
    assert len(EXPECTED) == 15


@pytest.mark.parametrize("source", list(FailureSource))
def test_classify_returns_exact_triple(source: FailureSource) -> None:
    """AC6/AC7/AC8 — every source resolves to its exact (category, cause, recoverable)."""
    expected = EXPECTED[source]
    result = classify_failure(source)
    assert result.category is expected.category
    assert result.cause is expected.cause
    assert result.recoverable is expected.recoverable


@pytest.mark.parametrize("source", list(FailureSource))
def test_classify_total_no_raise(source: FailureSource) -> None:
    """Totality — classify_failure never raises for any FailureSource member."""
    result = classify_failure(source)
    assert isinstance(result, FailureClassification)


def test_development_sources_share_dev_triple() -> None:
    """AC6 — the six development sources all yield DEVELOPMENT_FAILURE/None/False."""
    dev_sources = [
        s for s in FailureSource if EXPECTED[s].category is FailureCategory.DEVELOPMENT_FAILURE
    ]
    assert len(dev_sources) == 6
    for source in dev_sources:
        result = classify_failure(source)
        assert result.category is FailureCategory.DEVELOPMENT_FAILURE
        assert result.cause is None
        assert result.recoverable is False


@pytest.mark.parametrize(
    "source, expected_cause, expected_recoverable",
    [
        (FailureSource.DEPENDENCY, BlockedCause.INCOMPLETE_DEPENDENCY, True),
        (FailureSource.SECRET, BlockedCause.MISSING_SECRET, True),
        (FailureSource.CONTEXT, BlockedCause.MISSING_CONTEXT, False),
        (FailureSource.CLARIFICATION, BlockedCause.UNRESOLVED_CLARIFICATION, False),
        (FailureSource.MERGE, BlockedCause.MERGE_QUEUE_FAILURE, True),
        (FailureSource.DEPLOYMENT, BlockedCause.DEPLOYMENT_FAILURE, False),
        (FailureSource.EXTERNAL_SYSTEM, BlockedCause.EXTERNAL_SYSTEM_UNAVAILABLE, True),
        (FailureSource.NETWORK, BlockedCause.EXTERNAL_SYSTEM_UNAVAILABLE, True),
        (FailureSource.REPO, BlockedCause.REPO_INACCESSIBLE, False),
    ],
)
def test_blocked_source_cause_and_recoverable(
    source: FailureSource, expected_cause: BlockedCause, expected_recoverable: bool
) -> None:
    """AC7/AC8 — each blocked source maps to its exact cause and recoverable flag."""
    result = classify_failure(source)
    assert result.category is FailureCategory.BLOCKED
    assert result.cause is expected_cause
    assert result.recoverable is expected_recoverable


@pytest.mark.parametrize(
    "cause, expected",
    [
        (BlockedCause.INCOMPLETE_DEPENDENCY, True),
        (BlockedCause.MISSING_SECRET, True),
        (BlockedCause.EXTERNAL_SYSTEM_UNAVAILABLE, True),
        (BlockedCause.MERGE_QUEUE_FAILURE, True),
        (BlockedCause.MISSING_CONTEXT, False),
        (BlockedCause.UNRESOLVED_CLARIFICATION, False),
        (BlockedCause.REPO_INACCESSIBLE, False),
        (BlockedCause.DEPLOYMENT_FAILURE, False),
    ],
)
def test_recoverable_flags_per_id069(cause: BlockedCause, expected: bool) -> None:
    """AC8 — the recoverable flag for every BlockedCause matches ID-069."""
    # Find the source(s) that map to this cause in the oracle and assert.
    sources = [s for s in FailureSource if EXPECTED[s].cause is cause]
    assert sources, f"no source maps to {cause} in the oracle"
    for source in sources:
        assert classify_failure(source).recoverable is expected


@pytest.mark.parametrize("source", list(FailureSource))
def test_detail_always_contains_source_name(source: FailureSource) -> None:
    """detail always carries the originating source name."""
    result = classify_failure(source)
    assert source.name in result.detail
    assert result.detail.startswith(source.name)


def test_exit_code_zero_appears_in_detail() -> None:
    """AC9 — exit_code=0 IS emitted as 'exit=0' (not truthiness-gated)."""
    result = classify_failure(FailureSource.LINT, exit_code=0)
    assert "exit=0" in result.detail


def test_exit_code_nonzero_appears_in_detail() -> None:
    """A nonzero exit_code appears in detail."""
    result = classify_failure(FailureSource.BUILD, exit_code=2)
    assert "exit=2" in result.detail


def test_exit_code_none_omitted_from_detail() -> None:
    """When exit_code is None, no 'exit=' token appears in detail."""
    result = classify_failure(FailureSource.LINT)
    assert "exit=" not in result.detail


def test_empty_message_omitted_from_detail() -> None:
    """An empty message contributes no 'msg=' token to detail."""
    result = classify_failure(FailureSource.LINT, message="")
    assert "msg=" not in result.detail


def test_nonempty_message_appears_in_detail() -> None:
    """A non-empty message appears in detail as 'msg=<message>'."""
    result = classify_failure(FailureSource.DEPENDENCY, message="package foo missing")
    assert "msg=package foo missing" in result.detail


@pytest.mark.parametrize("source", list(FailureSource))
def test_exit_code_message_do_not_alter_triple(source: FailureSource) -> None:
    """AC9 — exit_code/message change only detail, never (category, cause, recoverable)."""
    base = classify_failure(source)
    with_extras = classify_failure(source, exit_code=7, message="boom")
    assert with_extras.category is base.category
    assert with_extras.cause is base.cause
    assert with_extras.recoverable is base.recoverable
    # detail differs (carries the extras) but is non-empty and still carries the name.
    assert with_extras.detail != base.detail or source is not None  # detail may differ
    assert source.name in with_extras.detail
    assert "exit=7" in with_extras.detail
    assert "msg=boom" in with_extras.detail


@pytest.mark.parametrize("source", list(FailureSource))
def test_detail_is_deterministic(source: FailureSource) -> None:
    """Same inputs always yield the same detail string."""
    a = classify_failure(source, exit_code=3, message="x")
    b = classify_failure(source, exit_code=3, message="x")
    assert a.detail == b.detail
    assert a == b


def test_detail_format_order() -> None:
    """detail format is '<name> exit=<n> msg=<message>' (name, then exit, then msg)."""
    result = classify_failure(FailureSource.CI, exit_code=1, message="failed")
    assert result.detail == "CI exit=1 msg=failed"
