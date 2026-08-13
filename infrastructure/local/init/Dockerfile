# Local init container image (SFP-79).
#
# One-shot provisioner: waits for Postgres + LocalStack readiness, runs
# ``pulumi up`` against LocalStack (SFP-66 program), then runs ``alembic upgrade
# head`` per logical DB. Built standalone from this directory; the workspace
# repo (services + Pulumi program) is mounted at /workspace at run time, so the
# image only carries the provisioning code + its Python deps.

FROM python:3.13-slim

# Pulumi CLI (the program itself is delivered by SFP-66). Installed via the
# official installer script; pinned to the v3 line. Skipped silently by the
# runner if absent (SFP-66 not yet integrated) — see run_pulumi.py.
ADD https://get.pulumi.com/install.sh /tmp/pulumi-install.sh
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && sh /tmp/pulumi-install.sh --version 3.* \
    && apt-get purge -y --auto-remove curl \
    && rm -rf /var/lib/apt/lists/*
ENV PATH="/root/.pulumi/bin:${PATH}"

WORKDIR /app

# Install Python dependencies first (better layer caching than copying code).
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy the provisioning code (helpers + one-shot runners + entrypoint).
COPY logging_utils.py service_registry.py readiness.py /app/
COPY run_pulumi.py run_alembic.py /app/
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh /app/run_pulumi.py /app/run_alembic.py

# The workspace repo is mounted here at run time (services + Pulumi program).
# service_registry.REPO_ROOT defaults to /workspace via the SFP_REPO_ROOT env
# set in compose.yaml.
VOLUME ["/workspace"]

ENV SFP_REPO_ROOT=/workspace \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# One-shot: run the provisioning orchestration, then exit.
ENTRYPOINT ["/app/entrypoint.sh"]
