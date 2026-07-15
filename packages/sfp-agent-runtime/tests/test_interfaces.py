"""Tests for the agent-runtime abstraction interfaces (SFP-51).

Covers the acceptance criteria:
- (a) ``AgentRuntime`` and ``PromptProvider`` are runtime-checkable Protocols;
- (b) a trivial concrete implementation satisfies each interface (``isinstance``)
      and produces conformant output when exercised;
- (c) the IO dataclasses construct, apply defaults, and are frozen;
- (d) importing the module pulls in NO vendor SDK and the source text contains
      no banned vendor references (AP-010 / MAS §9.6).
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path
from typing import Protocol as TypingProtocol

import pytest
import sfp_agent_runtime.interfaces as iface_module
from sfp_agent_runtime.interfaces import (
    AgentRunRequest,
    AgentRunResult,
    AgentRuntime,
    PromptProvider,
)

# Importable module names that would indicate a vendor SDK was pulled in.
BANNED_MODULES = ("anthropic", "openai")
# Source-text tokens that indicate a vendor reference (matches the SFP-51 verify
# grep: ``grep -iE "anthropic|openai|claude"``).
BANNED_SOURCE_TOKENS = ("anthropic", "openai", "claude")
SOURCE_PATH = Path(iface_module.__file__)


# --- (a) interfaces are runtime-checkable Protocols -------------------------


def test_interfaces_subclass_protocol() -> None:
    """(a) Both interfaces subclass typing.Protocol."""
    assert issubclass(AgentRuntime, TypingProtocol)
    assert issubclass(PromptProvider, TypingProtocol)


def test_interfaces_are_runtime_checkable() -> None:
    """(a) Both interfaces are decorated runtime_checkable (isinstance-able)."""
    assert getattr(AgentRuntime, "_is_runtime_protocol", False) is True
    assert getattr(PromptProvider, "_is_runtime_protocol", False) is True


# --- (b) concrete implementations satisfy the interfaces -------------------


class _StubRuntime:
    """Minimal duck-typed implementation of ``AgentRuntime``."""

    def run(self, request: AgentRunRequest) -> AgentRunResult:
        return AgentRunResult(
            agent=request.agent,
            ticket_id=request.ticket_id,
            success=True,
            output={"echo": request.prompt},
        )


class _StubPromptProvider:
    """Minimal duck-typed implementation of ``PromptProvider``."""

    def get_prompt(self, agent: str, task: str) -> str:
        return f"prompt for {agent}/{task}"


def test_concrete_runtime_is_instance_of_interface() -> None:
    """(b) A duck-typed object is recognized as an AgentRuntime."""
    assert isinstance(_StubRuntime(), AgentRuntime)


def test_concrete_prompt_provider_is_instance_of_interface() -> None:
    """(b) A duck-typed object is recognized as a PromptProvider."""
    assert isinstance(_StubPromptProvider(), PromptProvider)


def test_stub_runtime_run_returns_conformant_result() -> None:
    """(b) The concrete run() produces a conformant AgentRunResult."""
    request = AgentRunRequest(agent="coder", ticket_id="SFP-51", prompt="hi")
    result = _StubRuntime().run(request)
    assert isinstance(result, AgentRunResult)
    assert result.success is True
    assert result.agent == "coder"
    assert result.ticket_id == "SFP-51"
    assert result.output == {"echo": "hi"}
    assert result.error is None


def test_stub_prompt_provider_returns_resolved_text() -> None:
    """(b) The concrete get_prompt() returns the resolved prompt text."""
    assert _StubPromptProvider().get_prompt("planner", "plan") == "prompt for planner/plan"


def test_object_missing_method_is_not_instance_of_interface() -> None:
    """(b) An object lacking the method does NOT satisfy the Protocol."""
    assert not isinstance(object(), AgentRuntime)
    assert not isinstance(object(), PromptProvider)


# --- (c) IO dataclasses ----------------------------------------------------


def test_agent_run_request_applies_default_context() -> None:
    """(c) ``context`` defaults to an empty mapping."""
    request = AgentRunRequest(agent="coder", ticket_id="SFP-51", prompt="hi")
    assert dict(request.context) == {}


def test_agent_run_request_carries_supplied_context() -> None:
    """(c) Supplied context is preserved verbatim."""
    request = AgentRunRequest(agent="coder", ticket_id="SFP-51", prompt="hi", context={"k": 1})
    assert dict(request.context) == {"k": 1}


def test_agent_run_result_defaults_to_none() -> None:
    """(c) ``output`` and ``error`` default to None on a successful result."""
    result = AgentRunResult(agent="coder", ticket_id="SFP-51", success=True)
    assert result.output is None
    assert result.error is None


def test_agent_run_result_failure_carries_error() -> None:
    """(c) A failed run carries an error message and no output."""
    result = AgentRunResult(agent="coder", ticket_id="SFP-51", success=False, error="boom")
    assert result.output is None
    assert result.error == "boom"


def test_io_models_are_dataclasses() -> None:
    """(c) The IO models are dataclasses."""
    assert dataclasses.is_dataclass(AgentRunRequest)
    assert dataclasses.is_dataclass(AgentRunResult)


def test_agent_run_request_is_frozen() -> None:
    """(c) The request dataclass is frozen (immutable)."""
    request = AgentRunRequest(agent="coder", ticket_id="SFP-51", prompt="hi")
    with pytest.raises(dataclasses.FrozenInstanceError):
        request.agent = "planner"  # type: ignore[misc]


# --- (d) no vendor SDK -----------------------------------------------------


def test_importing_module_loads_no_vendor_sdk() -> None:
    """(d) No banned vendor SDK is present in sys.modules after import."""
    # The module is imported at the top of this file; re-import is a no-op, so
    # assert none of the banned packages are currently loaded.
    loaded = {name.split(".")[0] for name in sys.modules}
    for banned in BANNED_MODULES:
        assert banned not in loaded, f"vendor SDK {banned!r} was imported"


def test_source_contains_no_vendor_references() -> None:
    """(d) The interfaces source text contains no banned vendor tokens."""
    text = SOURCE_PATH.read_text().lower()
    for token in BANNED_SOURCE_TOKENS:
        assert token not in text, f"banned vendor reference {token!r} present in interfaces source"
