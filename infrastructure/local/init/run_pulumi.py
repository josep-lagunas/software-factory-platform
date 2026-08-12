#!/usr/bin/env python3
"""``pulumi up`` invoker for LocalStack (SNS/SQS) for the local init container (SFP-79).

One-shot provisioning script (coverage-excluded, per the existing
``migrations/env.py`` precedent — see ``tool.coverage.run.omit`` in
``pyproject.toml``).

Shells out to the ``pulumi`` CLI (the Pulumi local program itself is delivered
by SFP-66, which is out of scope here) against the LocalStack endpoint injected
by compose. Idempotent: ``pulumi up`` against existing state is a safe
incremental apply (Pulumi's normal behaviour), so the container is safe to
re-run on restart.

Sequential after the data layer and readiness checks, before Alembic
(MAS §12.7 determinism — NEVER parallel).

If the Pulumi program directory (``PULUMI_PROGRAM_DIR``) or the ``pulumi`` binary
is absent, the step logs a WARNING and is skipped (exit 0): SFP-66 is an upstream
dependency and may not yet be integrated. A real ``pulumi up`` failure (program
present but errored) is a hard failure — the caller aborts the init run.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from logging_utils import configure_logging, log_step

logger = configure_logging(os.environ.get("INIT_LOG_LEVEL", "INFO"))

#: Directory of the Pulumi local program (SFP-66). Configurable; the default is
#: the conventional location under the local-infra tree.
PULUMI_PROGRAM_DIR = Path(
    os.environ.get("PULUMI_PROGRAM_DIR", "/workspace/infrastructure/local/pulumi")
)

#: LocalStack edge endpoint, injected by compose (service-name DNS from inside
#: the compose network).
LOCALSTACK_ENDPOINT = os.environ.get("LOCALSTACK_ENDPOINT", "http://localstack:4566")

#: AWS region used by the Pulumi program against LocalStack (informational; the
#: program reads its own config — this only seeds the subprocess environment).
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")


def _program_available(program_dir: Path) -> bool:
    """Return whether the Pulumi program directory looks usable."""
    return program_dir.is_dir() and any(program_dir.iterdir())


def _build_env() -> dict[str, str]:
    """Build the subprocess env pointing Pulumi/AWS clients at LocalStack."""
    env = dict(os.environ)
    env["AWS_ENDPOINT_URL"] = LOCALSTACK_ENDPOINT
    env["AWS_ACCESS_KEY_ID"] = env.get("AWS_ACCESS_KEY_ID", "test")
    env["AWS_SECRET_ACCESS_KEY"] = env.get("AWS_SECRET_ACCESS_KEY", "test")
    env["AWS_REGION"] = AWS_REGION
    env["LOCALSTACK_ENDPOINT"] = LOCALSTACK_ENDPOINT
    return env


def main() -> int:
    log_step(logger, "pulumi up: begin")

    if not _program_available(PULUMI_PROGRAM_DIR):
        logger.warning(
            "pulumi: program dir %s absent/empty — skipping "
            "(SFP-66 Pulumi program not yet integrated)",
            PULUMI_PROGRAM_DIR,
        )
        return 0

    if shutil.which("pulumi") is None:
        logger.warning("pulumi: `pulumi` binary not on PATH — skipping (SFP-66 not integrated)")
        return 0

    log_step(logger, f"pulumi up: {PULUMI_PROGRAM_DIR} (endpoint={LOCALSTACK_ENDPOINT})")
    # ``--yes`` skips the interactive confirmation (non-interactive container).
    # ``--skip-preview`` avoids the separate preview pass; the apply is the
    # provision step. Idempotent against existing state.
    result = subprocess.run(  # noqa: S603 — trusted, well-defined argv
        ["pulumi", "up", "--yes", "--skip-preview"],
        cwd=str(PULUMI_PROGRAM_DIR),
        env=_build_env(),
        check=False,
    )
    if result.returncode != 0:
        logger.error("pulumi up failed with exit code %d", result.returncode)
        return result.returncode

    log_step(logger, "pulumi up: LocalStack topics/queues provisioned")
    return 0


if __name__ == "__main__":
    sys.exit(main())
