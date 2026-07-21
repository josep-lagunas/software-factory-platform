"""The deterministic failure classifier (SFP-75; ID-068/ID-069).

This module is the *logic* half of SFP-75 — the pure
:func:`classify_failure` function that maps a
:class:`~sfp_contracts.workflow.failure.FailureSource` (plus optional
``exit_code`` / ``message``) to a fully-resolved
:class:`~sfp_contracts.workflow.failure.FailureClassification`. It depends on the
contract in :mod:`sfp_contracts.workflow.failure` (the workspace-worker declares
``sfp-contracts`` as a dependency).

Grounded in:
- ID-068 — the source -> cause mapping (which :class:`BlockedCause` each blocked
  :class:`FailureSource` resolves to; development sources classify to
  :attr:`~sfp_contracts.workflow.failure.FailureCategory.DEVELOPMENT_FAILURE`).
- ID-069 — the per-cause ``recoverable`` flags (auto-recoverable vs
  human-recoverable). :func:`classify_failure` only *computes* the flag; acting
  on it (retry / CONFIRM) is the Orchestrator's responsibility.
- SFP-75 REPO decision — :attr:`FailureSource.REPO` maps to
  :attr:`BlockedCause.REPO_INACCESSIBLE`, making the function total over
  :class:`FailureSource`.

Design choice (R3): the source -> cause mapping and the per-cause recoverable
flags are expressed as module-level data (``_SOURCE_TO_CAUSE``,
``_RECOVERABLE``, ``_DEVELOPMENT_SOURCES``) — NOT an inline ``if``/``elif``
ladder — so the ID-068/ID-069 table is auditable in one place. The function
itself is a pure lookup; ``exit_code`` and ``message`` are informational and only
contribute to the ``detail`` string (they never alter category/cause/recoverable).
"""

from sfp_contracts.workflow.failure import (
    BlockedCause,
    FailureCategory,
    FailureClassification,
    FailureSource,
)

#: Development sources classify to DEVELOPMENT_FAILURE (cause=None,
#: recoverable=False). Listed here as data so the partition is auditable.
_DEVELOPMENT_SOURCES: frozenset[FailureSource] = frozenset(
    {
        FailureSource.LINT,
        FailureSource.TYPECHECK,
        FailureSource.BUILD,
        FailureSource.UNIT_TEST,
        FailureSource.INTEGRATION_TEST,
        FailureSource.CI,
    }
)

#: Maps each blocked :class:`FailureSource` to its :class:`BlockedCause`
#: (ID-068). NETWORK and EXTERNAL_SYSTEM both resolve to
#: EXTERNAL_SYSTEM_UNAVAILABLE; REPO resolves to REPO_INACCESSIBLE (SFP-75).
_SOURCE_TO_CAUSE: dict[FailureSource, BlockedCause] = {
    FailureSource.DEPENDENCY: BlockedCause.INCOMPLETE_DEPENDENCY,
    FailureSource.SECRET: BlockedCause.MISSING_SECRET,
    FailureSource.CONTEXT: BlockedCause.MISSING_CONTEXT,
    FailureSource.CLARIFICATION: BlockedCause.UNRESOLVED_CLARIFICATION,
    FailureSource.MERGE: BlockedCause.MERGE_QUEUE_FAILURE,
    FailureSource.DEPLOYMENT: BlockedCause.DEPLOYMENT_FAILURE,
    FailureSource.EXTERNAL_SYSTEM: BlockedCause.EXTERNAL_SYSTEM_UNAVAILABLE,
    FailureSource.NETWORK: BlockedCause.EXTERNAL_SYSTEM_UNAVAILABLE,
    FailureSource.REPO: BlockedCause.REPO_INACCESSIBLE,
}

#: Per-cause recoverable flags (ID-069). ``True`` = auto-recoverable (retried
#: once the condition clears); ``False`` = human-recoverable (CONFIRM flow).
_RECOVERABLE: dict[BlockedCause, bool] = {
    BlockedCause.INCOMPLETE_DEPENDENCY: True,
    BlockedCause.MISSING_SECRET: True,
    BlockedCause.EXTERNAL_SYSTEM_UNAVAILABLE: True,
    BlockedCause.MERGE_QUEUE_FAILURE: True,
    BlockedCause.MISSING_CONTEXT: False,
    BlockedCause.UNRESOLVED_CLARIFICATION: False,
    BlockedCause.REPO_INACCESSIBLE: False,
    BlockedCause.DEPLOYMENT_FAILURE: False,
}


def _build_detail(source: FailureSource, exit_code: int | None, message: str) -> str:
    """Compose the deterministic, informational ``detail`` string.

    Format: ``<source.name> [exit=<n>] [msg=<message>]``, space-joined.

    - ``exit_code`` is included when it is *not* ``None`` — i.e. ``exit=0`` IS
      emitted (this deliberately tests against truthiness-based bugs).
    - ``message`` is included only when non-empty.
    - The string is built from plain concatenation and cannot raise; it never
      echoes secrets because the only inputs are the source name, an integer, and
      a caller-supplied informational message (callers must not pass secrets
      here — the parameter is named ``message``, not ``secret``).
    """
    parts: list[str] = [source.name]
    if exit_code is not None:
        parts.append(f"exit={exit_code}")
    if message:
        parts.append(f"msg={message}")
    return " ".join(parts)


def classify_failure(
    source: FailureSource,
    *,
    exit_code: int | None = None,
    message: str = "",
) -> FailureClassification:
    """Classify a reported failure source into a :class:`FailureClassification`.

    The mapping is total over :class:`FailureSource`:

    - **Development sources** (LINT, TYPECHECK, BUILD, UNIT_TEST,
      INTEGRATION_TEST, CI) -> ``category=DEVELOPMENT_FAILURE``,
      ``cause=None``, ``recoverable=False``.
    - **Blocked sources** -> ``category=BLOCKED``, ``cause=_SOURCE_TO_CAUSE[source]``,
      ``recoverable=_RECOVERABLE[cause]`` (ID-068/ID-069).

    ``exit_code`` and ``message`` are **informational only**: they contribute to
    ``detail`` and do NOT alter ``category``, ``cause``, or ``recoverable``.

    Args:
        source: The originating failure source (a :class:`FailureSource` member).
        exit_code: Optional process exit code to surface in ``detail``. ``0`` and
            ``None`` are distinguished: ``exit_code=0`` emits ``exit=0`` while
            ``None`` emits nothing.
        message: Optional informational message to surface in ``detail``. Omitted
            from ``detail`` when empty. Must not carry secret material.

    Returns:
        The deterministic :class:`FailureClassification`.
    """
    detail = _build_detail(source, exit_code, message)

    if source in _DEVELOPMENT_SOURCES:
        return FailureClassification(
            category=FailureCategory.DEVELOPMENT_FAILURE,
            cause=None,
            recoverable=False,
            detail=detail,
        )

    cause = _SOURCE_TO_CAUSE[source]
    return FailureClassification(
        category=FailureCategory.BLOCKED,
        cause=cause,
        recoverable=_RECOVERABLE[cause],
        detail=detail,
    )
