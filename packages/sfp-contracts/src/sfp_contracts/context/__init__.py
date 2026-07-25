"""Context contracts: the versioned registry of cross-ticket context types.

This package is introduced by SFP-36. It exposes the :class:`ContextCatalogue`
and the :class:`~sfp_contracts.context.types.ContextTypeKind` marker enum
(including the ``secret_ref`` kind, ID-016) so that tickets can advertise the
*names and kinds* of context values they produce or consume without ever
materialising secret values themselves. The catalogue is versioned (ID-071) so
additions don't break older tickets.

This package also exposes :class:`ContextBinding` and :class:`ResolvedContext`
(SFP-66): the typed value schemas that carry completed dependencies' outputs and
the resolved/missing split produced when the Readiness Gate walks a ticket's
declared required inputs against those bindings.
"""

__all__ = ["ContextBinding", "ResolvedContext"]

from .bindings import ContextBinding, ResolvedContext
