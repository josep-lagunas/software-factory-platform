"""Opaque secret references for the composition root.

A :class:`SecretRef` is a *reference* to a secret, never the secret value
itself. Resolution of the referenced value happens at runtime by a dedicated
provider (the local provider in SFP-12, the AWS Secrets Manager provider in
SFP-78). This keeps secret material out of process memory and out of
configuration objects (ID-016, MAS §10.8 — "Application code never reads
encrypted configuration directly").
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict


class SecretRef(BaseModel):
    """An opaque, immutable reference to a secret.

    A SecretRef is an opaque reference; secret values are resolved at runtime
    by a provider (SFP-12/SFP-78), never held here (ID-016, MAS §10.8).

    Attributes:
        name: Opaque secret identifier, e.g. a secret name or ARN fragment.
        version: Optional version/pinning pointer. ``None`` means "current".
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    name: str
    version: str | None = None

    def __repr__(self) -> str:
        version_repr = repr(self.version) if self.version is not None else "None"
        return f"SecretRef(name={self.name!r}, version={version_repr})"

    def __str__(self) -> str:
        version_str = self.version if self.version is not None else "None"
        return f"SecretRef(name={self.name}, version={version_str})"
