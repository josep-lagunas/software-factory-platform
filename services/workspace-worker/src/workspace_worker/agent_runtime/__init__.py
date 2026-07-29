"""Concrete AgentRuntime adapter package (SFP-36 / Jira SFP-53).

The single vendor-aware seam above :mod:`sfp_agent_runtime` —
:class:`~workspace_worker.agent_runtime.runtime.ClaudeAgentRuntime` wraps the
Claude Agent SDK. Everything above the sfp-agent-runtime seam stays vendor-free
(AP-010 / MAS §9.6); this package is the one place a vendor SDK is imported.
"""
