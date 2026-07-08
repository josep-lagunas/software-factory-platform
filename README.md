# Software Factory Platform (SFP)

SFP is a multi-agent system that automates the spec-to-PR pipeline.

## Phase A environment setup

Copy the template and fill in real values:

    cp .env.example .env

Then load the environment into your shell:

    source ./source-env.sh

The loader auto-exports every key in `.env` (via `set -a`), so lines need no
`export` prefix. To point at a different file, set `SFP_ENV_FILE`:

    SFP_ENV_FILE=/path/to/.env source ./source-env.sh
