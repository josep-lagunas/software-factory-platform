"""The orchestrator-side Context Resolver Host (SFP-151; SFP-85 line).

Turns a ticket's :class:`~sfp_contracts.context.declaration.TicketContextDeclaration`
into an in-memory :class:`~sfp_contracts.context.bindings.ResolvedContext` for
the coding emitter (SFP-152). This is glue over the landed contracts
(``sfp_contracts.context``, SFP-36/37/66) and the landed secret machinery
(``sfp_config``, SFP-12): it restates no catalogue logic and invents no policy
(MAS §12.9).

Grounded in:
- ID-016 / MAS §10.8 — secret material is resolved through the provider seam
  and lives **only** in the returned in-memory
  :class:`~sfp_contracts.context.bindings.ContextBinding.value`. It is never
  logged, never persisted, and never interpolated into an error message. Note
  that the landed bindings carry the *reference* string for ``SECRET_REF``
  types by design (ID-071); this host is the sanctioned materialization point
  named by the PRSpec — the materialized value goes to the emitter's memory
  and nowhere else.
- SFP-85 — the resolver sits orchestrator-side, ahead of job creation.
- Fail-closed (MAS §12.9 / PRSpec) — any unresolved requirement raises
  :class:`MissingContextError`; there is no partial-success shape and the
  ``missing`` list is never populated on failure.
- Determinism (MAS §12.7) — ``resolve()`` iterates ``required_inputs`` in
  declaration order, holds no shared mutable state, and reads no clock,
  random, or network source. The same inputs always yield the same output or
  the same error.

Seams (constructor-injected; ID-072 — no service-locator, no globals):

- ``secrets`` — a structural ``SecretProvider`` Protocol
  (``resolve(ref: SecretRef) -> str``) matching the landed
  ``sfp_config.providers.SecretProvider`` surface exactly. The Protocol is
  declared here so this module needs **no runtime sfp_config dependency**;
  only the structural test imports ``sfp_config.LocalSecretProvider``, which
  is why ``sfp-config`` is a declared (test-time) orchestrator dependency.
- ``values_source`` — ``Callable[[str], dict[str, str]]`` mapping the
  ticket/spec id to its declared context values. Where those values live is
  the blueprint funnel's question, deliberately out of scope here.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, runtime_checkable

from sfp_config.secrets import SecretRef
from sfp_contracts.context.bindings import ContextBinding, ResolvedContext
from sfp_contracts.context.declaration import TicketContextDeclaration
from sfp_contracts.context.types import (
    DEFAULT_CATALOGUE,
    ContextType,
    ContextTypeKind,
)

__all__ = ["ContextResolverHost", "MissingContextError", "SecretProvider"]

#: The declared-values seam: ticket/spec id → declared context values.
ValuesSource = Callable[[str], dict[str, str]]

#: The catalogue lookup index, derived once from :data:`DEFAULT_CATALOGUE`.
#: ``ContextCatalogue`` is a Pydantic model (``schema_version`` + ``entries``)
#: with no name-keyed accessor of its own (see landed ``types.py``), so the
#: documented-lookup equivalent is a name→type index built from ``entries`` —
#: the same shape ``declaration.py`` derives for output validation.
_CATALOGUE_BY_NAME: dict[str, ContextType] = {
    entry.name: entry for entry in DEFAULT_CATALOGUE.entries
}


@runtime_checkable
class SecretProvider(Protocol):
    """Structural seam: resolves an opaque ``SecretRef`` to its plaintext value.

    Matches the landed ``sfp_config.providers.SecretProvider`` surface exactly
    (``resolve(ref: SecretRef) -> str``, raising ``SecretResolutionError`` on
    failure). Declared here so this module carries no runtime ``sfp_config``
    dependency beyond :class:`~sfp_config.secrets.SecretRef`; implementations
    satisfy it structurally — the acceptance test proves the landed
    ``LocalSecretProvider`` does.
    """

    def resolve(self, ref: SecretRef) -> str:
        """Resolve ``ref`` to its plaintext value.

        Args:
            ref: The opaque secret reference to resolve.

        Returns:
            The resolved secret value.

        Raises:
            SecretResolutionError: If the reference cannot be resolved. The
                error carries only the reference and a source label — never
                secret material (see landed ``sfp_config.providers``).
        """
        ...  # pragma: no cover - Protocol method body, never executed


class MissingContextError(Exception):
    """A declared context requirement could not be resolved (SFP-151).

    Raised fail-closed — the message names **only** the missing item's
    reference/name, never any secret material. Provider failures are chained
    (``raise ... from exc``) for debugging, but the chained
    ``SecretResolutionError`` text is never interpolated into this message:
    this class constructs its message from the item name alone.
    """

    def __init__(self, name: str) -> None:
        #: The name of the unresolved context requirement (never a value).
        self.name = name
        super().__init__(f"Missing context input: {name}")


class ContextResolverHost:
    """Resolve a declaration's ``required_inputs`` into a ``ResolvedContext``.

    The host holds no mutable state; every :meth:`resolve` call builds its
    bindings fresh. Constructor seams are the injected secret provider and the
    declared-values source (see the module docstring).

    Total-failure semantics: the first unresolved requirement raises
    :class:`MissingContextError` — no partial context is returned and
    ``missing`` is never populated. A declaration with zero
    ``required_inputs`` resolves successfully to the empty-but-valid context.
    """

    def __init__(self, secrets: SecretProvider, values_source: ValuesSource) -> None:
        self._secrets = secrets
        self._values_source = values_source

    def resolve(self, pr_spec_id: str, declaration: TicketContextDeclaration) -> ResolvedContext:
        """Resolve ``declaration``'s ``required_inputs`` for ``pr_spec_id``.

        Args:
            pr_spec_id: The ticket/spec id; becomes the returned context's
                ``ticket_id`` and keys the values-source lookup.
            declaration: The ticket's declared context I/O contract. Only
                ``required_inputs`` participates here.

        Returns:
            A fully resolved :class:`ResolvedContext` — one
            :class:`ContextBinding` per required input, in declaration order,
            with ``missing`` empty by construction.

        Raises:
            MissingContextError: A required input has no declared value, or a
                secret reference could not be materialized. The message names
                only the item; the first failure aborts (no partial success).
        """
        result = ResolvedContext(ticket_id=pr_spec_id)
        values = self._values_source(pr_spec_id)

        for item in declaration.required_inputs:
            if item.name not in values:
                raise MissingContextError(item.name)

            declared = values[item.name]
            ctx_type = _CATALOGUE_BY_NAME.get(item.name)
            if ctx_type is None:
                # Documented default (declaration.py): required-input names
                # are free-form — cross-ticket satisfaction is the Readiness
                # Gate's runtime job, not the catalogue's. No error here.
                ctx_type = ContextType(name=item.name, kind=ContextTypeKind.STR)

            if ctx_type.kind is ContextTypeKind.SECRET_REF:
                # Materialize: the declared value IS the reference string.
                # Material lives only in the binding's in-memory value — it
                # is never logged, persisted, or embedded in an error.
                try:
                    value = self._secrets.resolve(SecretRef(name=declared))
                except Exception as exc:
                    # Message constructed from the reference NAME only; the
                    # provider's text is chained, never interpolated.
                    raise MissingContextError(item.name) from exc
            else:
                value = declared

            result.resolved.append(
                ContextBinding(
                    name=item.name,
                    context_type=ctx_type,
                    value=value,
                    source_ticket=item.source_ticket,
                )
            )

        return result
