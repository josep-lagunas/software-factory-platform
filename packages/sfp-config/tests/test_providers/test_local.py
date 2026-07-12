"""Tests for :class:`LocalSecretProvider` (env/file secret resolution).

The provider resolves an opaque :class:`~sfp_config.secrets.SecretRef` from the
process environment first, then a gitignored local ``key=value`` file. These
tests pin that resolution order, the value-free failure mode, and the read-once
cache — using ``tmp_path``/``monkeypatch`` so the real environment and working
directory are never touched.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sfp_config.providers import SecretProvider, SecretResolutionError
from sfp_config.providers.local import LocalSecretProvider
from sfp_config.secrets import SecretRef


def _write_secrets(path: Path, lines: list[str]) -> Path:
    """Write ``lines`` to ``path`` and return it (always newline-terminated)."""
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_env_hit_returns_env_value(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("API_TOKEN", "from-env")
    provider = LocalSecretProvider(secrets_file=tmp_path / "secrets.local")
    assert provider.resolve(SecretRef(name="API_TOKEN")) == "from-env"


def test_file_hit_returns_file_value(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("API_TOKEN", raising=False)
    secrets = _write_secrets(tmp_path / "secrets.local", ["API_TOKEN=from-file"])
    provider = LocalSecretProvider(secrets_file=secrets)
    assert provider.resolve(SecretRef(name="API_TOKEN")) == "from-file"


def test_env_overrides_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("API_TOKEN", "from-env")
    secrets = _write_secrets(tmp_path / "secrets.local", ["API_TOKEN=from-file"])
    provider = LocalSecretProvider(secrets_file=secrets)
    assert provider.resolve(SecretRef(name="API_TOKEN")) == "from-env"


def test_empty_env_value_is_a_miss_that_falls_through_to_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # An empty env var is falsy and must not mask a real file entry.
    monkeypatch.setenv("API_TOKEN", "")
    secrets = _write_secrets(tmp_path / "secrets.local", ["API_TOKEN=from-file"])
    provider = LocalSecretProvider(secrets_file=secrets)
    assert provider.resolve(SecretRef(name="API_TOKEN")) == "from-file"


def test_empty_file_value_is_a_miss(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("API_TOKEN", raising=False)
    secrets = _write_secrets(tmp_path / "secrets.local", ["API_TOKEN="])
    provider = LocalSecretProvider(secrets_file=secrets)
    with pytest.raises(SecretResolutionError):
        provider.resolve(SecretRef(name="API_TOKEN"))


def test_absent_file_with_env_hit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # An absent file simply contributes no entries; env still resolves.
    monkeypatch.setenv("API_TOKEN", "from-env")
    provider = LocalSecretProvider(secrets_file=tmp_path / "does-not-exist")
    assert provider.resolve(SecretRef(name="API_TOKEN")) == "from-env"


def test_absent_file_and_missing_env_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("MISSING", raising=False)
    provider = LocalSecretProvider(secrets_file=tmp_path / "does-not-exist")
    with pytest.raises(SecretResolutionError):
        provider.resolve(SecretRef(name="MISSING"))


def test_malformed_line_without_equals_is_skipped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("GOOD", raising=False)
    # A line without '=' is skipped, not fatal; GOOD still resolves.
    secrets = _write_secrets(tmp_path / "secrets.local", ["this-line-has-no-equals", "GOOD=ok"])
    provider = LocalSecretProvider(secrets_file=secrets)
    assert provider.resolve(SecretRef(name="GOOD")) == "ok"


def test_comment_and_blank_lines_are_skipped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("GOOD", raising=False)
    secrets = _write_secrets(tmp_path / "secrets.local", ["# a comment", "", "   ", "GOOD=ok"])
    provider = LocalSecretProvider(secrets_file=secrets)
    assert provider.resolve(SecretRef(name="GOOD")) == "ok"


def test_whitespace_stripped_around_keys_and_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("KEY", raising=False)
    secrets = _write_secrets(tmp_path / "secrets.local", ["  KEY  =  spaced  "])
    provider = LocalSecretProvider(secrets_file=secrets)
    assert provider.resolve(SecretRef(name="KEY")) == "spaced"


def test_version_is_ignored(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Local dev has no versioned secrets; version must not affect resolution.
    monkeypatch.setenv("API_TOKEN", "v")
    provider = LocalSecretProvider(secrets_file=tmp_path / "secrets.local")
    ref_no_version = SecretRef(name="API_TOKEN")
    ref_versioned = SecretRef(name="API_TOKEN", version="v42")
    assert provider.resolve(ref_no_version) == provider.resolve(ref_versioned) == "v"


def test_resolution_error_message_is_value_free(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("MISSING", raising=False)
    # Another real value exists in the file; it must not leak into the error.
    secrets = _write_secrets(tmp_path / "secrets.local", ["OTHER=super-secret-value"])
    provider = LocalSecretProvider(secrets_file=secrets)
    with pytest.raises(SecretResolutionError) as exc_info:
        provider.resolve(SecretRef(name="MISSING"))
    message = str(exc_info.value)
    assert "super-secret-value" not in message
    # The reference name and source label are safe to surface.
    assert "MISSING" in message
    assert exc_info.value.ref.name == "MISSING"
    assert exc_info.value.source.startswith("env-or-file:")


def test_provider_satisfies_secret_provider_protocol() -> None:
    provider = LocalSecretProvider()
    assert isinstance(provider, SecretProvider)


def test_default_secrets_file_path() -> None:
    # No path given -> the conventional gitignored filename in the cwd.
    provider = LocalSecretProvider()
    assert provider.secrets_file == Path("secrets.local")
    assert LocalSecretProvider.DEFAULT_SECRETS_FILE == Path("secrets.local")


def test_custom_secrets_file_path(tmp_path: Path) -> None:
    custom = tmp_path / "custom-secrets"
    provider = LocalSecretProvider(secrets_file=custom)
    assert provider.secrets_file == custom


def test_file_is_read_at_most_once(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # The cache must prevent a second disk read: after the first resolve, the
    # file can disappear and the cached value is still returned.
    monkeypatch.delenv("API_TOKEN", raising=False)
    secrets = _write_secrets(tmp_path / "secrets.local", ["API_TOKEN=from-file"])
    provider = LocalSecretProvider(secrets_file=secrets)
    assert provider.resolve(SecretRef(name="API_TOKEN")) == "from-file"
    secrets.unlink()  # remove the backing file
    # Second resolve still returns the cached value -> file was not re-read.
    assert provider.resolve(SecretRef(name="API_TOKEN")) == "from-file"
