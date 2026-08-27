"""Typed business facts for the core-loop policies (MAS §8.14, SFP-143).

Each policy consumes one typed fact model. The SFP-142 engine seam carries
facts as a ``Sequence[str]`` (the engine records what was considered; it never
interprets it), so each model provides a deterministic ``to_fact_strings()``
that renders it into the engine's fact-string vocabulary — ``"<kind>:<field>:<value>"``.

The fact *kinds* here are deliberately **distinct** from the SFP-138/SFP-139
driver kinds. The drivers render whole observations as
``"<kind>:<summary>"`` (``plan-fact:…``, ``coding-job-fact:<status>``,
``pr-fact:…``, ``review-fact:<pr>:<status>``); these policies render
per-field booleans and so need their own, unambiguous kinds — in particular
``review-outcome-fact`` below must not collide with the landed driver's
``review-fact`` kind, which carries a different grammar. A policy class
exposes ``parse_fact`` to lift these strings back into the typed model, so
``(state, fact model)`` and its engine-level ``(state, fact strings)``
rendering are the *same* input expressed two ways.

Grounded in MAS §12.9: fact shapes are taken from what is landed — the
:class:`~sfp_contracts.agents.reviewer.ReviewStatus` vocabulary and the
``sfp_contracts.commands`` payload names — never invented.

All models are frozen and ``extra="forbid"``: a fact is an immutable
observation, and schema drift between observer and policy surfaces immediately.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

#: The fact-kind prefix for coding-start facts in the engine's string vocabulary.
CODING_START_FACT_KIND = "coding-start-fact"

#: The fact-kind prefix for review facts in the engine's string vocabulary.
#: Distinct from the SFP-139 driver's ``review-fact:<pr>:<status>`` kind, which
#: summarizes an observed review record under a different grammar.
REVIEW_FACT_KIND = "review-outcome-fact"

#: The fact-kind prefix for merge-ready facts in the engine's string vocabulary.
MERGE_READY_FACT_KIND = "merge-ready-fact"


class CodingStartFact(BaseModel):
    """The business facts :class:`~.coding_start.CodingStartPolicy` consumes.

    Shape (per the PRSpec): whether the coding-start request was *admitted*
    (a valid ``ExecuteCodingJob``-style request: the PR-spec is executable and
    admission checks passed) and whether coding *capacity* is available. Both
    must hold for the policy to decide ``CODING_IN_PROGRESS``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Whether an ``ExecuteCodingJob``-style start request was admitted.
    start_request_admitted: bool
    #: Whether a coder slot is free to take the job right now.
    capacity_available: bool

    def to_fact_strings(self) -> tuple[str, ...]:
        """Render deterministically into the engine's fact-string vocabulary."""
        return (
            f"{CODING_START_FACT_KIND}:start_request_admitted:{self.start_request_admitted}",
            f"{CODING_START_FACT_KIND}:capacity_available:{self.capacity_available}",
        )


class ReviewStatus(StrEnum):
    """The review verdicts the review policy routes on.

    Mirrors the landed :class:`sfp_contracts.agents.reviewer.ReviewStatus`
    vocabulary one-for-one (the four terminal verdicts the Reviewer can
    return). Declared here as a local StrEnum so the policies package stays
    importable without pulling the reviewer's richer output models; the values
    are asserted equal to the landed enum by tests, so drift fails loudly.
    """

    APPROVED = "APPROVED"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    BLOCKED = "BLOCKED"
    NEEDS_HUMAN_DECISION = "NEEDS_HUMAN_DECISION"


class ReviewFact(BaseModel):
    """The business facts :class:`~.review_success.ReviewSuccessPolicy` consumes.

    Shape (per the PRSpec): the reviewer's verdict, using the landed
    ``ReviewStatus`` vocabulary. ``APPROVED`` and ``CHANGES_REQUESTED`` route
    the workflow forward (merge / ID-068 rework respectively); ``BLOCKED`` and
    ``NEEDS_HUMAN_DECISION`` are recorded non-moves that reference the
    escalation command — deciding *only*; driving ``WAITING_FOR_USER`` is
    SFP-141's concern.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: The reviewer's verdict (landed ``ReviewStatus`` vocabulary).
    review_status: ReviewStatus

    def to_fact_strings(self) -> tuple[str, ...]:
        """Render deterministically into the engine's fact-string vocabulary."""
        return (f"{REVIEW_FACT_KIND}:review_status:{self.review_status.value}",)


class MergeReadyFact(BaseModel):
    """The business facts :class:`~.merge_ready.MergeReadyPolicy` consumes.

    Shape (per the PRSpec): per-concern booleans — the PR's review approval
    for the *current* head, and the CI/gates status — so a missing one is
    nameable in the recorded reason. ``False`` here means *observed and not
    satisfied*; the "not observed yet" case is the fact model being absent,
    which the policy records with its own absent-fact reason.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Whether the PR's review is APPROVED **for the current head commit**.
    pr_review_approved_for_head: bool
    #: Whether every CI / validation gate is green.
    ci_gates_green: bool

    def to_fact_strings(self) -> tuple[str, ...]:
        """Render deterministically into the engine's fact-string vocabulary."""
        return (
            f"{MERGE_READY_FACT_KIND}:pr_review_approved_for_head:{self.pr_review_approved_for_head}",
            f"{MERGE_READY_FACT_KIND}:ci_gates_green:{self.ci_gates_green}",
        )


__all__ = [
    "CODING_START_FACT_KIND",
    "MERGE_READY_FACT_KIND",
    "REVIEW_FACT_KIND",
    "CodingStartFact",
    "MergeReadyFact",
    "ReviewFact",
    "ReviewStatus",
]
