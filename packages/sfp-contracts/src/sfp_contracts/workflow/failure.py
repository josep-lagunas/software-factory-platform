"""The failure-classification taxonomy (SFP-75; ID-068/ID-069).

This module is the *contract* half of SFP-75 — the pure taxonomy and the
:class:`FailureClassification` pydantic model. The mapping *function*
(:func:`workspace_worker.workflow.failure.classify_failure`) lives in the
workspace-worker service and consumes these types.

Grounded in:
- ID-068 — the failure-classification taxonomy: a stage terminates either as a
  :attr:`FailureCategory.DEVELOPMENT_FAILURE` (the agent/coder must fix it) or as
  :attr:`FailureCategory.BLOCKED` (an external cause the agent cannot resolve
  alone), and each blocked termination carries one of eight
  :class:`BlockedCause` values.
- ID-069 — the recoverable flags: which blocked causes are auto-recoverable
  (retried) vs human-recoverable (CONFIRM flow). The flag is carried on the
  classification; acting on it is the Orchestrator's job, not this contract's.
- ID-013 — ``StrEnum`` with ``value == name`` so JSON serialization yields the
  plain string member name.
- SFP-75 — REPO decision: :attr:`FailureSource.REPO` is added by this ticket so
  that :attr:`BlockedCause.REPO_INACCESSIBLE` is reachable from
  ``classify_failure`` (the taxonomy is total over ``FailureSource``).

Design choices (mirroring the sibling schemas in :mod:`sfp_contracts.agents` and
:mod:`sfp_contracts.context.types`):
- Every enum member has ``value == name`` (ID-013).
- :class:`FailureClassification` uses ``extra='forbid'`` so schema drift surfaces
  immediately.
- :meth:`FailureClassification.to_json` / :meth:`FailureClassification.from_json`
  delegate to pydantic, mirroring
  :class:`sfp_contracts.agents.reviewer.ReviewerOutput`.
"""

from enum import StrEnum
from typing import ClassVar

from pydantic import BaseModel, ConfigDict


class FailureCategory(StrEnum):
    """The two top-level reasons a stage terminates (ID-068).

    ``DEVELOPMENT_FAILURE`` — the agent/coder produced a failing result (lint,
    typecheck, build, tests, CI); the agent must fix its own work. Never blocked,
    never recoverable through external unblocking.

    ``BLOCKED`` — an external cause prevents progress that the agent cannot
    resolve alone (a missing dependency, missing secret, unreachable repo, etc.);
    the specific cause is carried in :attr:`FailureClassification.cause`.
    """

    DEVELOPMENT_FAILURE = "DEVELOPMENT_FAILURE"
    BLOCKED = "BLOCKED"


class BlockedCause(StrEnum):
    """The eight causes of a :attr:`FailureCategory.BLOCKED` termination (ID-068).

    Members (in ID-068 order), each ``value == name``:

    - ``INCOMPLETE_DEPENDENCY`` — a upstream ticket/PR the work depends on has not
      landed; auto-recoverable once it does (ID-069).
    - ``MISSING_CONTEXT`` — a required cross-ticket context value is absent;
      human-recoverable (the human must supply it).
    - ``MISSING_SECRET`` — a referenced secret is not materialised in the
      environment; auto-recoverable once the secret store is populated.
    - ``REPO_INACCESSIBLE`` — the git repository cannot be reached or cloned;
      human-recoverable (infra/access issue).
    - ``UNRESOLVED_CLARIFICATION`` — an open clarification the agent raised has
      not been answered; human-recoverable.
    - ``MERGE_QUEUE_FAILURE`` — the PR failed in the merge queue; auto-recoverable
      (re-queue once the queue condition clears).
    - ``DEPLOYMENT_FAILURE`` — a deployment step failed; human-recoverable
      (requires investigation, not a blind retry).
    - ``EXTERNAL_SYSTEM_UNAVAILABLE`` — a required external system (Jira, GitHub,
      a registry) is unavailable; auto-recoverable (retry once it recovers).
    """

    INCOMPLETE_DEPENDENCY = "INCOMPLETE_DEPENDENCY"
    MISSING_CONTEXT = "MISSING_CONTEXT"
    MISSING_SECRET = "MISSING_SECRET"
    REPO_INACCESSIBLE = "REPO_INACCESSIBLE"
    UNRESOLVED_CLARIFICATION = "UNRESOLVED_CLARIFICATION"
    MERGE_QUEUE_FAILURE = "MERGE_QUEUE_FAILURE"
    DEPLOYMENT_FAILURE = "DEPLOYMENT_FAILURE"
    EXTERNAL_SYSTEM_UNAVAILABLE = "EXTERNAL_SYSTEM_UNAVAILABLE"


class FailureSource(StrEnum):
    """The 15 originating sources a failure can be reported from (ID-068).

    Members are partitioned into two groups, each ``value == name``:

    Development group — classify to
    :attr:`FailureCategory.DEVELOPMENT_FAILURE`:

    - ``LINT``, ``TYPECHECK``, ``BUILD``, ``UNIT_TEST``,
      ``INTEGRATION_TEST``, ``CI``.

    Blocked group — classify to :attr:`FailureCategory.BLOCKED` with a mapped
    :class:`BlockedCause`:

    - ``DEPENDENCY``, ``SECRET``, ``CONTEXT``, ``CLARIFICATION``, ``MERGE``,
      ``DEPLOYMENT``, ``EXTERNAL_SYSTEM``, ``NETWORK``, ``REPO``.

    ``REPO`` is added by SFP-75 (see the REPO decision, R1) so that
    :attr:`BlockedCause.REPO_INACCESSIBLE` is reachable from
    ``classify_failure``; without it the taxonomy would not be total over
    ``BlockedCause``.
    """

    # --- development group ---
    LINT = "LINT"
    TYPECHECK = "TYPECHECK"
    BUILD = "BUILD"
    UNIT_TEST = "UNIT_TEST"
    INTEGRATION_TEST = "INTEGRATION_TEST"
    CI = "CI"
    # --- blocked group ---
    DEPENDENCY = "DEPENDENCY"
    SECRET = "SECRET"
    CONTEXT = "CONTEXT"
    CLARIFICATION = "CLARIFICATION"
    MERGE = "MERGE"
    DEPLOYMENT = "DEPLOYMENT"
    EXTERNAL_SYSTEM = "EXTERNAL_SYSTEM"
    NETWORK = "NETWORK"
    REPO = "REPO"


class FailureClassification(BaseModel):
    """A classified failure: category, optional blocked cause, and detail.

    Fields:
        category: The top-level reason the stage terminated
            (:class:`FailureCategory`).
        cause: The specific :class:`BlockedCause` when ``category`` is
            :attr:`FailureCategory.BLOCKED`; ``None`` for
            :attr:`FailureCategory.DEVELOPMENT_FAILURE` (a development failure
            has no external cause).
        recoverable: Whether the failure is auto-recoverable (retried) per
            ID-069. Carried as data only; acting on it (retry / CONFIRM) is the
            Orchestrator's responsibility, not this model's.
        detail: A deterministic, informational human-readable string. Built from
            the originating source name plus optional ``exit_code``/``message``;
            it MUST NOT echo secrets and MUST NOT alter category/cause/recoverable.

    Unknown fields are rejected (``extra='forbid'``), mirroring the sibling agent
    and context schemas.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    category: FailureCategory
    cause: BlockedCause | None = None
    recoverable: bool
    detail: str = ""

    def to_json(self) -> str:
        """Serialize this classification to a JSON string (delegates to pydantic)."""
        return self.model_dump_json()

    @classmethod
    def from_json(cls, data: str | bytes) -> "FailureClassification":
        """Deserialize a :class:`FailureClassification` from a JSON string or bytes."""
        return cls.model_validate_json(data)
