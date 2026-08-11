"""Deterministic build-order DAG logic (SFP-195).

The wave-based build-order computation over the parsed ticket graph: index
build, dangling-dependency check, longest-path wave assignment with cycle
detection, and the ready-set selector (``--done`` mode). Promoted verbatim —
same collect-all / longest-path / Kahn semantics — from
``tools/build_order.py`` (SFP-195) into this typed, CI-mypy-checked module, as
the second step (promo 2 of 3) of the ``tools/`` -> ``sfp_contracts`` effort
that began with the PRSpec linter (PR #122, SFP-236). The CLI stays as a thin
wrapper over these functions plus the parser (``create_jira_tickets``) and the
output emitters.

Wave assignment is memoized longest-path::

    wave(t) = 0               if t has no deps
    wave(t) = 1 + max(wave(d)) otherwise

Cycles are detected via a DFS recursion stack. Where the original tool
``sys.exit``-ed on a cycle (process-killing glue that made the logic
un-importable), this module raises :class:`BuildOrderCycleError` carrying the
formatted cycle chain. The thin CLI catches it and reproduces the identical
exit message, so external CLI behaviour is byte-identical.

The concrete consumer is the Orchestrator scheduler (SFP-128 / SFP-129), which
calls :func:`compute_ready` / :func:`compute_waves` to pick the next unblocked
ticket. Operating on *parsed* ticket dicts (produced by
``create_jira_tickets.parse_hierarchy``), this module never parses markdown and
never touches the filesystem.

Non-goal: the ticket dict is NOT replaced with a pydantic model here — the
graph functions remain pure stdlib over the parser's output. A model swap is a
separate consideration, explicitly deferred.
"""

from __future__ import annotations

from typing import TypedDict

# ============================================================
# CONTRACT — the parsed-ticket shape this module operates on
# ============================================================


class ParsedTicket(TypedDict):
    """The subset of the ticket dict produced by
    ``create_jira_tickets.parse_hierarchy`` that the build-order logic reads.

    The parser populates a richer dict (``emoji``, ``labels``, ``description``,
    ``epic_id``); only the fields consumed here are declared, so the graph
    functions accept the parser's full output without forcing callers to
    project it down. Total/``required_keys`` is used (every key always present)
    so attribute access via ``t["num"]`` etc. type-checks under ``--strict``.
    """

    id: str
    num: int
    area: str
    executor: str
    title: str
    phase: str | None
    deps: list[str]


# ============================================================
# EXCEPTION — cycle detected during wave assignment
# ============================================================


class BuildOrderCycleError(ValueError):
    """Raised by :func:`compute_waves` when the dependency graph contains a
    cycle. ``args[0]`` carries the formatted cycle chain (e.g.
    ``"SFP-2 -> SFP-3 -> SFP-2"``) — the same string the CLI previously
    formatted for ``sys.exit``. Subclassing :class:`ValueError` keeps it
    catchable as a plain value error while letting the CLI single it out for
    its byte-identical ``error: cycle detected: ...`` message.
    """


# ============================================================
# INDEX + VALIDATION
# ============================================================


def build_index(tickets: list[ParsedTicket]) -> dict[int, ParsedTicket]:
    """Build a ``{num: ticket}`` index from the parsed ticket list."""
    return {t["num"]: t for t in tickets}


def check_dangling(
    tickets: list[ParsedTicket], by_num: dict[int, ParsedTicket]
) -> tuple[str, str] | None:
    """Return the first ``(offender_id, missing_dep)`` pair whose dep num is
    not in the index, or ``None`` if all deps resolve. Iterates tickets
    ascending by num.
    """
    for t in sorted(tickets, key=lambda x: x["num"]):
        for dep in t["deps"]:
            dep_num = int(dep.split("-")[1])
            if dep_num not in by_num:
                return (t["id"], dep)
    return None


def compute_waves(tickets: list[ParsedTicket], by_num: dict[int, ParsedTicket]) -> dict[int, int]:
    """Compute ``{num: wave}`` via memoized longest-path with DFS
    recursion-stack cycle detection.

    Raises :class:`BuildOrderCycleError` (carrying the formatted cycle chain)
    if a cycle is found — the thin CLI catches this and exits with the
    byte-identical ``error: cycle detected: ...`` message.
    """
    memo: dict[int, int] = {}
    stack: list[int] = []  # current recursion path (nums)
    on_stack: set[int] = set()  # O(1) membership for the current path

    def wave_of(num: int) -> int:
        if num in memo:
            return memo[num]
        if num in on_stack:
            # Cycle: the slice from the first occurrence to here is the cycle.
            idx = stack.index(num)
            cycle = stack[idx:]
            chain = " -> ".join(f"SFP-{n}" for n in cycle) + f" -> SFP-{num}"
            raise BuildOrderCycleError(chain)
        on_stack.add(num)
        stack.append(num)
        dep_nums = [int(d.split("-")[1]) for d in by_num[num]["deps"]]
        w = 0 if not dep_nums else 1 + max(wave_of(d) for d in dep_nums)
        on_stack.discard(num)
        stack.pop()
        memo[num] = w
        return w

    for t in sorted(tickets, key=lambda x: x["num"]):
        wave_of(t["num"])
    return memo


# ============================================================
# READY SET (--done mode)
# ============================================================


def compute_ready(tickets: list[ParsedTicket], done_set: set[str]) -> list[str]:
    """Tickets NOT in ``done_set`` whose deps are a subset of ``done_set``,
    lowest-num-first. ``done_set`` is a set of ticket-id strings (e.g.
    ``{"SFP-5", "SFP-6"}``).
    """
    ready: list[str] = []
    for t in tickets:
        if t["id"] in done_set:
            continue
        if set(t["deps"]) <= done_set:
            ready.append(t["id"])
    ready.sort(key=lambda tid: int(tid.split("-")[1]))
    return ready


__all__ = [
    "BuildOrderCycleError",
    "ParsedTicket",
    "build_index",
    "check_dangling",
    "compute_ready",
    "compute_waves",
]
