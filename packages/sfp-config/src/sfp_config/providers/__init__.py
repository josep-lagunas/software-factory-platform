"""Secret-resolution providers.

A :class:`~sfp_config.providers.SecretProvider` resolves an opaque
:class:`~sfp_config.secrets.SecretRef` to its plaintext value. The local
development provider (:class:`~sfp_config.providers.local.LocalSecretProvider`)
reads environment variables and a gitignored local file; the production
provider (AWS Secrets Manager) lands in SFP-78. The abstraction is what lets
local development avoid real AWS secrets entirely (ID-016, ID-054).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from sfp_config.secrets import SecretRef


class SecretResolutionError(Exception):
    """Raised when a :class:`SecretRef` cannot be resolved to a value.

    Carries only the reference (name/version), never a secret value, so it is
    safe to log or surface.
    """

    def __init__(self, ref: SecretRef, *, source: str) -> None:
        self.ref = ref
        self.source = source
        super().__init__(f"Secret not found: ref={ref!r} source={source}")


@runtime_checkable
class SecretProvider(Protocol):
    """Resolves an opaque :class:`SecretRef` to its plaintext value.

    Implementations MUST NOT log, print, or otherwise expose resolved secret
    values (ID-016, MAS §10.8 — "Application code never reads encrypted
    configuration directly"; the provider is the one sanctioned reader). A
    provider that cannot resolve a reference raises
    :class:`SecretResolutionError`.

    Note:
        This is a :class:`~typing.Protocol` so the production provider
        (SFP-78) can implement it structurally without importing this package
        at runtime.
    """

    def resolve(self, ref: SecretRef) -> str:
        """Resolve ``ref`` to its plaintext value.

        Args:
            ref: The opaque secret reference to resolve.

        Returns:
            The resolved secret value.

        Raises:
            SecretResolutionError: If the reference cannot be resolved.
        """
        ...


__all__ = ["SecretProvider", "SecretResolutionError"]
