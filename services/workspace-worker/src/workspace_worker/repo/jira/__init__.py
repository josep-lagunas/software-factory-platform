"""Jira subpackage — fetch issues + transition status via the Jira Cloud REST API.

Holds the Jira HTTP client (SFP-225) and the ADF -> :class:`ParsedTicket` parser
that backs it. The client mirrors :mod:`workspace_worker.repo.git` — injectable
:class:`httpx.Client`, tenacity retry of transient failures, redacted errors —
but authenticates with HTTP Basic (``base64(email:token)``), the credential
model the Jira Cloud REST API requires. The token is caller-resolved
(ID-016 / SFP-28) and never persisted.
"""

from workspace_worker.repo.jira.client import (
    JiraClient,
    JiraClientError,
    JiraIssueResult,
    JiraTransitionResult,
)
from workspace_worker.repo.jira.parser import adf_to_parsed_ticket

__all__ = [
    "JiraClient",
    "JiraClientError",
    "JiraIssueResult",
    "JiraTransitionResult",
    "adf_to_parsed_ticket",
]
