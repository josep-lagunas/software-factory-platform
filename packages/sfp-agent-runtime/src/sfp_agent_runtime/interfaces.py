"""Vendor-independent abstraction interfaces for the agent runtime.

This module is the vendor-neutral seam named by AP-010 / MAS §9.6: it defines
*what* the runtime does — run an agent to produce structured output, and resolve
the prompt for a given task — without naming or importing any vendor SDK. A
concrete provider (the SDK adapter, SFP-36) implements these interfaces;
everything above this seam stays vendor-free.

Grounded in:
- AP-010 / MAS §9.6 — the agent runtime is the vendor-neutral surface a concrete
  provider plugs into; the platform never imports a vendor SDK above this seam.
- SFP-51 (Jira) — the implementation ticket.

Design choices:
- :class:`typing.Protocol` (``runtime_checkable``) is preferred over
  :class:`abc.ABC` so a concrete provider satisfies the interface structurally
  (duck typing) without inheriting from it, and callers may ``isinstance``-check
  against the abstraction. ABCs would force a base-class coupling this seam
  deliberately avoids.
- The IO models are stdlib :func:`~dataclasses.dataclass` types rather than
  Pydantic models: this package declares no third-party runtime dependency, so
  the abstraction adds nothing to the lockfile. A provider may model its own
  outputs with Pydantic behind the interface.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class AgentRunRequest:
    """Vendor-neutral input to an :class:`AgentRuntime` run.

    Identifies *what* to run (the ``agent`` role plus its resolved ``prompt``
    text) and the working ``context`` the agent operates over. Carries no
    vendor-specific fields; a concrete adapter maps these onto its own SDK.

    Attributes:
        agent: The agent role being run (e.g. ``"planner"``, ``"coder"``).
        ticket_id: The ticket the run is working against.
        prompt: The fully-resolved prompt text (from a :class:`PromptProvider`
            or supplied directly by the caller).
        context: Resolved working context the agent operates over; opaque to the
            runtime (free-form key/value).
    """

    agent: str
    ticket_id: str
    prompt: str
    context: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AgentRunResult:
    """Vendor-neutral structured output of an :class:`AgentRuntime` run.

    Makes the run outcome explicit (``success``) without coupling to any
    vendor's error or usage types: on success ``output`` carries the opaque
    parsed-JSON result the agent produced; on failure ``error`` carries a
    provider-supplied message.

    Attributes:
        agent: The agent role that was run.
        ticket_id: The ticket the run worked against.
        success: ``True`` iff the run produced a usable result.
        output: The opaque parsed-JSON result on success; ``None`` on failure.
        error: A provider-supplied error message on failure; ``None`` on success.
    """

    agent: str
    ticket_id: str
    success: bool
    output: Mapping[str, Any] | None = None
    error: str | None = None


@runtime_checkable
class PromptProvider(Protocol):
    """Resolves the prompt text for a given agent and task.

    A concrete provider draws prompts from wherever the platform stores them
    (files, a registry, a templating layer); this interface hides that so the
    :class:`AgentRuntime` depends only on resolved text, not on prompt storage.
    """

    def get_prompt(self, agent: str, task: str) -> str:
        """Return the resolved prompt text for ``agent`` performing ``task``."""
        ...


@runtime_checkable
class AgentRuntime(Protocol):
    """Runs an agent and returns vendor-neutral structured output.

    This is the seam a concrete provider (the SDK adapter, SFP-36) implements;
    callers above the seam depend only on this interface (AP-010 / MAS §9.6),
    never on a vendor SDK.
    """

    def run(self, request: AgentRunRequest) -> AgentRunResult:
        """Run the agent described by ``request`` and return its structured result."""
        ...
