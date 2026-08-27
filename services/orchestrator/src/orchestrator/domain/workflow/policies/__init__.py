"""Core-loop workflow policies (MAS §8.14, SFP-143).

The three pure policies that decide the core loop's transitions:

- :class:`~.coding_start.CodingStartPolicy` — coding start
  (``READY_FOR_CODING → CODING_IN_PROGRESS``);
- :class:`~.review_success.ReviewSuccessPolicy` — review outcome, including
  the ID-068 rework loop
  (``REVIEW_IN_PROGRESS → READY_FOR_MERGE`` / ``→ CODING_IN_PROGRESS``);
- :class:`~.merge_ready.MergeReadyPolicy` — merge readiness
  (``READY_FOR_MERGE → MERGING``).

Each implements the landed SFP-142
:class:`~orchestrator.domain.workflow.policy_engine.WorkflowPolicy` protocol
and is consumed through
:func:`~orchestrator.domain.workflow.policy_engine.evaluate` — engine types are
imported, never forked. Policies **decide only**: no bus, no I/O, no clock, no
command execution; commands ride along as names (MAS §8.6) taken from the
landed ``sfp_contracts.commands`` payload classes, never invented here.

The typed business facts each policy consumes live in :mod:`.facts`.
"""

from orchestrator.domain.workflow.policies.coding_start import CodingStartPolicy
from orchestrator.domain.workflow.policies.facts import (
    CodingStartFact,
    MergeReadyFact,
    ReviewFact,
    ReviewStatus,
)
from orchestrator.domain.workflow.policies.merge_ready import MergeReadyPolicy
from orchestrator.domain.workflow.policies.review_success import ReviewSuccessPolicy

__all__ = [
    "CodingStartFact",
    "CodingStartPolicy",
    "MergeReadyFact",
    "MergeReadyPolicy",
    "ReviewFact",
    "ReviewStatus",
    "ReviewSuccessPolicy",
]
