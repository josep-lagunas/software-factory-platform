"""Build-planning contracts: the deterministic build-order DAG.

SFP-195 is the deterministic build-order generator (the ``tools/build_order.py``
CLI), and ID-021 / the in-progress ``tools/`` -> ``sfp_contracts`` promotion
effort move its load-bearing *logic* (the four pure functions over the parsed
ticket graph) into this typed, CI-mypy-checked module — mirroring the earlier
promotion of the PRSpec linter into :mod:`sfp_contracts.validation.prspec`
(PR #122, SFP-236). The CLI stays as a thin wrapper that owns parsing
(``create_jira_tickets.parse_hierarchy``) and output formatting only.

The concrete consumer of this module is the Orchestrator scheduler
(SFP-128 / SFP-129), which needs :func:`compute_ready` and :func:`compute_waves`
to pick the next unblocked ticket. Promoting the logic here makes it importable
by the scheduler without dragging in the CLI's ``argparse`` / file-emit glue,
and makes it answerable to the ``mypy --strict`` gate (``tools/`` is outside
CI's mypy scope, so type errors there were previously invisible).

Non-goals: this module operates on *parsed* ticket dicts and does NOT parse
markdown (parsing stays in ``tools/create_jira_tickets.py``). It does NOT emit
files (``emit_md`` / ``emit_json`` are CLI-output glue and remain in the tool).
It does NOT replace the dict-based ticket shape with a pydantic model — the
graph functions are pure stdlib over the parser's output, and a model swap is a
separate consideration, explicitly deferred.
"""

from sfp_contracts.planning.build_order import (
    BuildOrderCycleError,
    ParsedTicket,
    build_index,
    check_dangling,
    compute_ready,
    compute_waves,
)

__all__ = [
    "BuildOrderCycleError",
    "ParsedTicket",
    "build_index",
    "check_dangling",
    "compute_ready",
    "compute_waves",
]
