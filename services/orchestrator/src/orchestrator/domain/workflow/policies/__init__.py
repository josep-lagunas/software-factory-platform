"""Workflow policies (MAS §8.14, SFP-143/SFP-144).

The six pure policies that decide the workflow's transitions:

- :class:`~.coding_start.CodingStartPolicy` — coding start
  (``READY_FOR_CODING → CODING_IN_PROGRESS``);
- :class:`~.review_success.ReviewSuccessPolicy` — review outcome, including
  the ID-068 rework loop
  (``REVIEW_IN_PROGRESS → READY_FOR_MERGE`` / ``→ CODING_IN_PROGRESS``);
- :class:`~.merge_ready.MergeReadyPolicy` — merge readiness
  (``READY_FOR_MERGE → MERGING``);
- :class:`~.user_approval.UserApprovalPolicy` — user approval before merge
  (``MERGING → WAITING_FOR_USER``, the LEVEL_2+ tiers per ID-024);
- :class:`~.deploy_begin.DeployBeginPolicy` — deploy begin
  (``MERGING → DEPLOYING``);
- :class:`~.should_fail.ShouldFailPolicy` — whether an observed failure is
  terminal (``*active* → FAILED``, per ID-068).

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
from orchestrator.domain.workflow.policies.deploy_begin import DeployBeginPolicy
from orchestrator.domain.workflow.policies.facts import (
    CodingStartFact,
    DeployBeginFact,
    FailureFact,
    MergeReadyFact,
    ReviewFact,
    ReviewStatus,
    UserApprovalFact,
)
from orchestrator.domain.workflow.policies.merge_ready import MergeReadyPolicy
from orchestrator.domain.workflow.policies.review_success import ReviewSuccessPolicy
from orchestrator.domain.workflow.policies.should_fail import ShouldFailPolicy
from orchestrator.domain.workflow.policies.user_approval import UserApprovalPolicy

__all__ = [
    "CodingStartFact",
    "CodingStartPolicy",
    "DeployBeginFact",
    "DeployBeginPolicy",
    "FailureFact",
    "MergeReadyFact",
    "MergeReadyPolicy",
    "ReviewFact",
    "ReviewStatus",
    "ReviewSuccessPolicy",
    "ShouldFailPolicy",
    "UserApprovalFact",
    "UserApprovalPolicy",
]
