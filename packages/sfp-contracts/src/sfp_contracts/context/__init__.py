"""Context contracts: the versioned registry of cross-ticket context types.

This package is introduced by SFP-36. It exposes the :class:`ContextCatalogue`
and the :class:`~sfp_contracts.context.types.ContextTypeKind` marker enum
(including the ``secret_ref`` kind, ID-016) so that tickets can advertise the
*names and kinds* of context values they produce or consume without ever
materialising secret values themselves. The catalogue is versioned (ID-071) so
additions don't break older tickets.
"""
