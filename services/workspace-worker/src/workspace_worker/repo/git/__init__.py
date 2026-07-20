"""Git Provider Adapter subpackage — branch + push via the GitHub REST API.

Holds the Git Provider HTTP adapter (SFP-58 / ID-035) that creates or updates a
remote GitHub ref via the REST API using an injectable :class:`httpx.Client`
with bearer-token auth. The token is caller-resolved (ID-016 / SFP-28) and never
persisted, mirroring ``workspace_worker.repo.manager``.
"""

from workspace_worker.repo.git.adapter import (
    GitDeleteResult,
    GitProviderAdapter,
    GitProviderAdapterError,
    GitPushResult,
    PullRequestResult,
)

__all__ = [
    "GitDeleteResult",
    "GitProviderAdapter",
    "GitProviderAdapterError",
    "GitPushResult",
    "PullRequestResult",
]
