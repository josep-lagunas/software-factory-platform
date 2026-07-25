"""The pure ticket-context resolver (SFP-66; ID-071).

This module is the *logic* half of SFP-66 — the pure :func:`resolve_context`
function that walks a ticket's declared ``required_inputs`` (in order) against
the available :class:`~sfp_contracts.context.bindings.ContextBinding` instances
and returns a :class:`~sfp_contracts.context.bindings.ResolvedContext` split
into ``resolved`` (bindings found) and ``missing`` (names not found), both in
declaration order. It depends on the contract in
:mod:`sfp_contracts.context.bindings` (the workspace-worker declares
``sfp-contracts`` as a dependency).

Grounded in:
- ID-071 — the ticket context contract: each ticket declares its required
  inputs; the Readiness Gate resolves them against completed dependencies'
  outputs. :func:`resolve_context` is the pure resolution step; acting on a
  non-empty ``missing`` list (requesting the fact via the CONFIRM flow, ID-069)
  is the Orchestrator's responsibility.
- SFP-66 — the implementation ticket.

Design choice: the function is pure — no I/O, no mutation of ``declaration`` or
``available``. ``ticket_id`` is keyword-only because
:class:`~sfp_contracts.context.declaration.TicketContextDeclaration` has no
``ticket_id`` field (a declaration is reusable across tickets), so the caller
must supply the ticket identity explicitly. Injection of resolved values into
the agent/PR context is a separate, downstream concern and is out of scope here.
"""

from sfp_contracts.context.bindings import ContextBinding, ResolvedContext
from sfp_contracts.context.declaration import TicketContextDeclaration


def resolve_context(
    declaration: TicketContextDeclaration,
    available: dict[str, ContextBinding],
    *,
    ticket_id: str,
) -> ResolvedContext:
    """Resolve a ticket's declared required inputs against available bindings.

    Walks ``declaration.required_inputs`` **in order**; for each input, if its
    ``name`` is present in ``available`` the binding is appended to ``resolved``,
    otherwise the name is appended to ``missing``. Returns a
    :class:`ResolvedContext` carrying both lists (in declaration order) plus the
    ``ticket_id``.

    The function is pure: it performs no I/O and does not mutate ``declaration``
    or ``available``. Injection of the resolved values into the agent / PR
    context is the caller's concern (out of scope; ID-071).

    Args:
        declaration: The ticket's declarative context I/O contract; only its
            ``required_inputs`` are read.
        available: The bindings currently available from completed dependencies,
            keyed by binding name.
        ticket_id: The identity of the ticket whose inputs are being resolved
            (keyword-only — a declaration carries no ticket identity).

    Returns:
        The :class:`ResolvedContext` (``resolved`` / ``missing`` split, in
        declaration order).
    """
    resolved: list[ContextBinding] = []
    missing: list[str] = []

    for inp in declaration.required_inputs:
        binding = available.get(inp.name)
        if binding is not None:
            resolved.append(binding)
        else:
            missing.append(inp.name)

    return ResolvedContext(ticket_id=ticket_id, resolved=resolved, missing=missing)
