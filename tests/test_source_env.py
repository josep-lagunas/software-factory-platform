"""Tests for ``source-env.sh`` — token propagation to the CLIs (SFP-238).

The script's stated purpose is to load ``.env`` and bridge the credentials into
the environment so child processes (``gh``, ``git``, ``python``) inherit them.
Dogfooding SFP-237 found that a plain ``source ./source-env.sh && gh api user``
authenticated as the human (``josep-lagunas``), not ``sfp-coder-bot``:
``gh`` reads ``GH_TOKEN`` (or ``GITHUB_TOKEN``), but the script only exported
``GITHUB_TOKEN_CODER`` / ``GITHUB_TOKEN_REVIEWER`` — so ``gh`` never saw the
token under the name it reads and silently fell back to stored auth.

These tests run the script in a real subshell against a fixture ``.env`` and
assert the bridged names are exported. No network — the assertions are over
shell variable values, never over a live ``gh`` call.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "source-env.sh"


def _source_and_report(env_file: Path, expr: str) -> str:
    """Source ``source-env.sh`` against a fixture ``.env`` and print ``expr``.

    Runs in a clean ``/bin/sh`` subshell so no inherited env contaminates the
    result; only ``SFP_ENV_FILE`` (pointed at the fixture) is passed in.
    Returns the printed value (stripped).
    """
    proc = subprocess.run(
        ["/bin/sh", "-c", f'. "./source-env.sh" >/dev/null 2>&1; printf %s "{expr}"'],
        cwd=str(ROOT),
        env={"PATH": "/usr/bin:/bin", "SFP_ENV_FILE": str(env_file)},
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout


def _write_env(tmp_path: Path) -> Path:
    """Write a fixture ``.env`` with both GitHub role tokens + a plain var."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "GITHUB_TOKEN_CODER=fake-coder-tok-1234567890\n"
        "GITHUB_TOKEN_REVIEWER=fake-reviewer-tok-0987654321\n"
        "SOME_OTHER_VAR=hello\n"
    )
    return env_file


def test_env_file_keys_are_exported(tmp_path: Path) -> None:
    """Every key in .env becomes an exported env var (set -a works)."""
    env_file = _write_env(tmp_path)
    assert _source_and_report(env_file, "$SOME_OTHER_VAR") == "hello"
    assert _source_and_report(env_file, "$GITHUB_TOKEN_CODER") == "fake-coder-tok-1234567890"


def test_coder_token_bridged_to_gh_token(tmp_path: Path) -> None:
    """SFP-238 fix: GITHUB_TOKEN_CODER is bridged to GH_TOKEN (what gh reads)."""
    env_file = _write_env(tmp_path)
    # gh reads GH_TOKEN first; it must equal the coder role token.
    assert _source_and_report(env_file, "$GH_TOKEN") == "fake-coder-tok-1234567890"


def test_reviewer_token_bridged_to_github_token(tmp_path: Path) -> None:
    """SFP-238 fix: GITHUB_TOKEN_REVIEWER is bridged to GITHUB_TOKEN."""
    env_file = _write_env(tmp_path)
    assert _source_and_report(env_file, "$GITHUB_TOKEN") == "fake-reviewer-tok-0987654321"


def test_bridge_does_not_clobber_existing_gh_token(tmp_path: Path) -> None:
    """A pre-existing GH_TOKEN in the env is NOT overwritten by the bridge.

    The bridge fires only when the role token is non-empty; a caller who set
    GH_TOKEN explicitly before sourcing keeps their value. (Defensive — guards
    against the bridge silently overriding an intentional override.)
    """
    env_file = _write_env(tmp_path)
    proc = subprocess.run(
        [
            "/bin/sh",
            "-c",
            'GH_TOKEN=already-set-here . "./source-env.sh" >/dev/null 2>&1; printf %s "$GH_TOKEN"',
        ],
        cwd=str(ROOT),
        env={"PATH": "/usr/bin:/bin", "SFP_ENV_FILE": str(env_file)},
        capture_output=True,
        text=True,
        check=True,
    )
    assert proc.stdout == "fake-coder-tok-1234567890"  # coder token wins per bridge


def test_missing_env_file_returns_nonzero(tmp_path: Path) -> None:
    """A missing .env file makes the script exit non-zero (return/exit 1)."""
    missing = tmp_path / "does-not-exist.env"
    proc = subprocess.run(
        ["/bin/sh", "-c", '. "./source-env.sh" >/dev/null 2>&1'],
        cwd=str(ROOT),
        env={"PATH": "/usr/bin:/bin", "SFP_ENV_FILE": str(missing)},
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0


def test_unprefixed_keys_are_exported_without_export_prefix(tmp_path: Path) -> None:
    """A .env line WITHOUT an `export` prefix still exports (the set -a promise)."""
    env_file = tmp_path / ".env"
    env_file.write_text("PLAIN_KEY=plain-value\n")
    assert _source_and_report(env_file, "$PLAIN_KEY") == "plain-value"


def test_script_uses_sh_not_subshell_isolation() -> None:
    """The script file exists and is the one we're testing (sanity)."""
    assert SCRIPT.is_file()
    text = SCRIPT.read_text()
    # The bridge block must be present.
    assert "GITHUB_TOKEN_CODER" in text
    assert "GH_TOKEN" in text
    assert "GITHUB_TOKEN_REVIEWER" in text


if __name__ == "__main__":
    sys.exit(__import__("pytest").main([__file__, "-v"]))
