"""Local-development secret provider.

Resolves a :class:`~sfp_config.secrets.SecretRef` from, in order:

1. an environment variable named by ``ref.name``; then
2. a key in a gitignored local secrets file (a flat ``key=value`` text file).

This lets local development supply provider/DB/webhook credentials without
touching AWS Secrets Manager (ID-016, ID-054). Production uses the Secrets
Manager provider (SFP-78); this provider MUST NOT be used outside local/dev.

Security notes:
    - Resolved values are never logged, printed, or otherwise exposed. The
      only thing surfaced on failure is the *reference* (name/version),
      never the value (ID-016, MAS §10.8).
    - The local secrets file is expected to be gitignored; the default path
      ``secrets.local`` is covered by the ``secrets.*`` rule in ``.gitignore``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import ClassVar

from sfp_config.providers import SecretProvider, SecretResolutionError
from sfp_config.secrets import SecretRef

_DEFAULT_SECRETS_FILE = Path("secrets.local")


class LocalSecretProvider(SecretProvider):
    """Resolves :class:`SecretRef` instances from the env or a local file.

    Resolution order (first hit wins; an empty value counts as "not found" so
    a blank env var cannot silently mask a real file entry):

    1. ``os.environ[ref.name]`` — the primary local source.
    2. The local secrets file (``key=value`` lines, ``#`` comments), looked up
       by ``ref.name``.

    Args:
        secrets_file: Path to the local secrets file. Defaults to
            ``secrets.local`` in the current working directory. The file is
            optional; an absent file simply contributes no entries.

    Raises:
        SecretResolutionError: If neither source resolves ``ref``.
    """

    #: Default local secrets filename; gitignored via the ``secrets.*`` rule.
    DEFAULT_SECRETS_FILE: ClassVar[Path] = _DEFAULT_SECRETS_FILE

    def __init__(self, secrets_file: Path | None = None) -> None:
        self._secrets_file = secrets_file if secrets_file is not None else _DEFAULT_SECRETS_FILE
        self._cache: dict[str, str] | None = None

    @property
    def secrets_file(self) -> Path:
        """The local secrets file path this provider reads from."""
        return self._secrets_file

    def resolve(self, ref: SecretRef) -> str:
        """Resolve ``ref`` to its plaintext value (env first, then file).

        Args:
            ref: The opaque secret reference. ``ref.name`` is the env-var name
                and the file key; ``ref.version`` is ignored by the local
                provider (local dev has no versioned secrets).

        Returns:
            The resolved secret value.

        Raises:
            SecretResolutionError: If the value is not found in either source.
        """
        # 1. Environment variable — primary local source.
        value = os.environ.get(ref.name)
        if value:
            return value

        # 2. Gitignored local secrets file (key=value).
        file_value = self._load_file().get(ref.name)
        if file_value:
            return file_value

        raise SecretResolutionError(ref, source=self._source_label())

    def _load_file(self) -> dict[str, str]:
        """Lazily read and cache the local secrets file as a dict.

        The file is a flat ``key=value`` text file. Blank lines and ``#``
        comments are skipped; surrounding whitespace is stripped from keys and
        values. Lines without ``=`` are skipped. The cache is populated on the
        first read only; an absent or empty file yields ``{}``.

        Returns:
            A mapping of key to value. Never ``None``.
        """
        if self._cache is not None:
            return self._cache

        entries: dict[str, str] = {}
        path = self._secrets_file
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            for raw in text.splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                entries[key.strip()] = val.strip()

        self._cache = entries
        return entries

    def _source_label(self) -> str:
        """Human-readable, value-free label for error messages."""
        return f"env-or-file:{self._secrets_file}"
