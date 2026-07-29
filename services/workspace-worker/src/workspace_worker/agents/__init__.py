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
"""

from workspace_worker.agents.planner import PlannerError, plan

__all__ = ["PlannerError", "plan"]
