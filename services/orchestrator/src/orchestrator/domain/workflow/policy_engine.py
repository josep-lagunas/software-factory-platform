"""The pure, deterministic workflow policy engine (MAS §8.14, SFP-142).

Grounded in:
- MAS §8.14 — policies are deterministic, side-effect-free functions of the
  current workflow state and observed business facts that decide which
  transition applies and which commands are emitted. This module is the
  **engine** that runs a policy (or an ordered policy set) and turns its
  verdict into a typed outcome; it never decides business outcomes itself.
- MAS §8.7 — determinism: identical (state, facts, policies) inputs always
  yield an identical outcome.
- MAS §8.8 — "no transition" is an observable, *recorded* outcome, never an
  exception and never a silent skip.
- MAS §8.6 — commands never modify workflow state; the engine only *carries*
  commands as data.
- MAS §8.12 — decisions are immutable history: frozen, ``extra='forbid'``.
- AP-011 — purity: no clock, no randomness, no I/O, no bus. Facts arrive as
  typed input.
- ID-013 — enums serialize as plain strings in any serialized field.
- SFP-137 — the state machine stays the sole executor/guard: this engine
  *decides* a target state; it never applies a transition and never widens
  legality. A decided target is still validated against the SFP-137 table so
  an out-of-table verdict surfaces here, at decide-time, as a typed error.
- SFP-143/SFP-144 — the concrete policies (coding-start, review-success,
  merge-ready) are *out of scope*; they plug in through
  :class:`WorkflowPolicy` and are never referenced by name in the engine.

Shape (per the PRSpec implementation notes):

- :class:`PolicyDecision` is the typed verdict *a single policy* returns:
  either a transition target plus the commands to emit, or an explicit
  no-transition value. Both variants are plain data; exactly one holds.
- :class:`PolicyOutcome` is the engine-level result: the applied policy, the
  transition target (``None`` for no-transition), and the commands as data.
  It wraps the (transition-target, commands) pairing without mutating the
  SFP-137 :class:`~orchestrator.domain.workflow.state_machine.WorkflowDecision`,
  which records an *executed* transition (§8.5) — a policy outcome precedes
  execution and is the decision to *request* one.
- :class:`WorkflowPolicy` is the pluggable seam: a pure
  ``decide(state, facts) -> PolicyDecision`` protocol. Policies are passed
  *in*; the engine never looks one up by name.
- :func:`evaluate` / :func:`evaluate_policy_set` are the pure evaluators.
  The policy set uses **first-match wins** — the simplest deterministic
  reduction rule, documented on the function. A set where no policy produces
  a move returns a recorded no-transition outcome (§8.8), never an exception.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from orchestrator.domain.workflow.state_machine import TRANSITIONS, IllegalTransitionError
from orchestrator.domain.workflow.states import WorkflowState

#: The recorded no-transition value (MAS §8.8). A first-class decision — not
#: ``None``, not an exception, not a silent skip — used both as the boolean
#: marker on verdicts/outcomes and as the plain-string target in serialization.
NO_TRANSITION: str = "NO_TRANSITION"


class PolicyDecision(BaseModel):
    """One policy's verdict for ``(current state, business facts)`` (MAS §8.14).

    Pure data in exactly one of two shapes:

    - transition verdict — ``target_state`` set, ``no_transition`` ``False``;
    - no-transition verdict (§8.8) — ``no_transition`` ``True``,
      ``target_state`` ``None``.

    A model validator enforces that exactly one holds. ``command_names`` are
    *names of* commands (MAS §5.3 catalogue entries the caller resolves
    later): the policy and this engine never build, dispatch, or execute them
    (§8.6). Frozen and ``extra='forbid'`` — a verdict, once rendered, is
    immutable (MAS §8.12).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    target_state: WorkflowState | None = None
    no_transition: bool = False
    reason: str = ""
    command_names: tuple[str, ...] = Field(default=())

    @model_validator(mode="after")
    def _exactly_one_shape(self) -> PolicyDecision:
        has_target = self.target_state is not None
        if self.no_transition and has_target:
            raise ValueError("a verdict cannot be both a transition and a no-transition")
        if not self.no_transition and not has_target:
            raise ValueError("a verdict must set either target_state or no_transition")
        return self

    @classmethod
    def transition_verdict(
        cls,
        target_state: WorkflowState,
        *,
        reason: str = "",
        command_names: Sequence[str] = (),
    ) -> PolicyDecision:
        """Build a transition verdict: move to ``target_state``, emit commands."""
        return cls(
            target_state=target_state,
            no_transition=False,
            reason=reason,
            command_names=tuple(command_names),
        )

    @classmethod
    def no_transition_verdict(cls, *, reason: str = "") -> PolicyDecision:
        """Build a no-transition verdict (§8.8): record the decline, do not move."""
        return cls(target_state=None, no_transition=True, reason=reason, command_names=())


class PolicyOutcome(BaseModel):
    """The engine's typed result: (transition target, commands) as data.

    Fields:

    - ``current_state`` / ``target_state`` — the decided move's endpoints;
      ``target_state`` is ``None`` exactly when ``no_transition`` is ``True``.
    - ``applied_policy`` — which policy decided (or ``"policy-set"`` when the
      set as a whole declined); MAS §8.5 lineage.
    - ``reason`` — why the verdict is what it is.
    - ``business_facts_considered`` — the facts the evaluation ran on.
    - ``command_names`` — the commands to emit, as names (§8.6: carried, never
      dispatched; there is no execution path in this module).

    Frozen and ``extra='forbid'`` (MAS §8.12). ``to_json()`` emits plain
    strings for states and the ``NO_TRANSITION`` marker (ID-013).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    current_state: WorkflowState
    target_state: WorkflowState | None = None
    no_transition: bool = False
    applied_policy: str
    reason: str = ""
    business_facts_considered: tuple[str, ...] = Field(default=())
    command_names: tuple[str, ...] = Field(default=())
    current_state_name: str = Field(default="")
    target_state_name: str = Field(default="")

    def to_json(self) -> str:
        """Serialize with plain-string states and the no-transition marker (ID-013)."""
        return json.dumps(
            {
                "current_state": self.current_state_name or self.current_state.name,
                "target_state": (
                    NO_TRANSITION if self.no_transition else (self.target_state_name or "")
                ),
                "no_transition": self.no_transition,
                "applied_policy": self.applied_policy,
                "reason": self.reason,
                "business_facts_considered": list(self.business_facts_considered),
                "command_names": list(self.command_names),
            },
            sort_keys=True,
        )


class WorkflowPolicy(Protocol):
    """The pluggable policy seam (MAS §8.14; SFP-143/SFP-144 implement it).

    A policy is a *pure* ``decide`` callable: (current workflow state, typed
    business facts) → :class:`PolicyDecision`. It must not perform I/O, read
    the clock, touch the bus, or execute commands — it only renders data.
    Structural (duck) protocol: any object with this ``decide`` signature
    satisfies it; the engine consumes whatever is handed to it and never
    instantiates or names a concrete policy itself.
    """

    def decide(
        self,
        current_state: WorkflowState,
        business_facts: Sequence[str],
    ) -> PolicyDecision:
        """Render this policy's verdict for the given state and facts."""
        ...  # pragma: no cover


def evaluate(
    policy: WorkflowPolicy,
    current_state: WorkflowState,
    business_facts: Sequence[str] = (),
    *,
    policy_name: str = "",
) -> PolicyOutcome:
    """Evaluate one policy against ``(current state, business facts)`` — pure.

    Deterministic (AP-011): the same ``(policy, state, facts)`` always yields
    an equal :class:`PolicyOutcome`. No bus, no clock, no I/O, no command
    execution — commands ride along as names only (§8.6). A no-transition
    verdict is returned as a recorded outcome (§8.8), never raised.

    Raises:
        IllegalTransitionError: if the decided target is not legal from
            ``current_state`` per the SFP-137 transition table — surfaced here,
            at decide-time, so a malformed policy is caught before it reaches
            the executor. The engine never widens legality.

    Returns:
        The typed outcome: the applied policy, the transition target (or the
        recorded no-transition), and the commands to emit as data.
    """
    verdict = policy.decide(current_state, business_facts)
    target = verdict.target_state
    if target is not None and target not in TRANSITIONS.get(current_state, frozenset()):
        raise IllegalTransitionError(current_state, target)

    target_name = NO_TRANSITION if target is None else target.name
    return PolicyOutcome(
        current_state=current_state,
        target_state=target,
        no_transition=verdict.no_transition,
        applied_policy=policy_name or type(policy).__name__,
        reason=verdict.reason,
        business_facts_considered=tuple(business_facts),
        command_names=verdict.command_names,
        current_state_name=current_state.name,
        target_state_name=target_name,
    )


def evaluate_policy_set(
    policies: Sequence[WorkflowPolicy],
    current_state: WorkflowState,
    business_facts: Sequence[str] = (),
    *,
    policy_names: Sequence[str] | None = None,
) -> PolicyOutcome:
    """Evaluate an **ordered** policy set — first transition verdict wins.

    The reduction rule, deliberately the simplest deterministic one:

    1. Policies are consulted strictly in the caller's given order (MAS §8.7:
       ordering is part of the input, never ambient state).
    2. The **first** policy that yields a transition verdict decides; later
       policies are not consulted (precedence = position in the sequence).
    3. A policy yielding a no-transition verdict does **not** stop the search
       — it only means *this* policy declines; the next one is asked.
    4. If every policy declines, the engine returns a recorded no-transition
       outcome (§8.8) attributed to the set as a whole — never an exception,
       never a silent skip.

    Pure and deterministic (AP-011): the same ``(policies, state, facts)``
    triple always yields an equal outcome. The winning policy's command names
    are carried as data, never dispatched (§8.6).
    """
    if policy_names is not None and len(policy_names) != len(policies):
        raise ValueError("policy_names must name every policy in the set")
    names = list(policy_names) if policy_names is not None else [type(p).__name__ for p in policies]

    for name, policy in zip(names, policies, strict=True):
        outcome = evaluate(
            policy,
            current_state,
            business_facts,
            policy_name=name,
        )
        if not outcome.no_transition:
            return outcome

    return PolicyOutcome(
        current_state=current_state,
        target_state=None,
        no_transition=True,
        applied_policy="policy-set",
        reason="no policy in the set produced a transition verdict",
        business_facts_considered=tuple(business_facts),
        command_names=(),
        current_state_name=current_state.name,
        target_state_name=NO_TRANSITION,
    )


__all__ = [
    "NO_TRANSITION",
    "PolicyDecision",
    "PolicyOutcome",
    "WorkflowPolicy",
    "evaluate",
    "evaluate_policy_set",
]
