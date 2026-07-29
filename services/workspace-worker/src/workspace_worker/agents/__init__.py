"""Model-side agent evaluators for the workspace-worker.

This package hosts the agents that drive an
:class:`~sfp_agent_runtime.interfaces.AgentRuntime` to produce a typed contract
payload from a parsed ticket + resolved context. It is the per-agent counterpart
to the deterministic workflow functions in
:mod:`workspace_worker.workflow`.

Members:

- :func:`workspace_worker.agents.planner.plan` (SFP-70 / DOC SFP-53) — the
  Planner evaluator; decomposes a ready ticket into a
  :class:`~sfp_contracts.agents.planner.PlannerOutput`.
- :func:`workspace_worker.agents.test_designer.design_tests` (SFP-71 / DOC SFP-54)
  — the Test Designer evaluator; designs a
  :class:`~sfp_contracts.agents.test_designer.TestDesignerOutput` test plan.
- :func:`workspace_worker.agents.coder.code` (SFP-72 / DOC SFP-55) — the Coder
  evaluator; implements one PR-spec and returns a
  :class:`~sfp_contracts.agents.coder.CoderOutput`.
"""

from workspace_worker.agents.coder import CoderError, code
from workspace_worker.agents.planner import PlannerError, plan
from workspace_worker.agents.test_designer import TestDesignerError, design_tests

__all__ = [
    "CoderError",
    "PlannerError",
    "TestDesignerError",
    "code",
    "design_tests",
    "plan",
]
