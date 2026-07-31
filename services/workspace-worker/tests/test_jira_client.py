"""Tests for :mod:`workspace_worker.repo.jira.client` — the Jira Cloud REST slice (SFP-225).

All tests inject an :class:`httpx.Client` built on :class:`httpx.MockTransport`,
so nothing ever touches ``*.atlassian.net``. Each handler asserts the request
shape (method, URL path, Basic header, JSON payload) and returns canned
responses, including the retry-then-succeed, retry-exhaust, and no-retry 4xx
paths. Mirrors the precedents in ``test_git_adapter.py``.

Load-bearing points proven here:
- auth is HTTP **Basic** ``base64(email:token)``, NOT Bearer (the auth test
  decodes the header to prove it);
- the ADF ``description`` returned by ``fetch_issue`` is parsed into a
  :class:`ParsedTicket` with the eight ID-070 sections populated;
- retry / redact / local-validation behaviours match ``GitProviderAdapter``.
"""

from __future__ import annotations

import base64
import dataclasses
import json
from collections.abc import Callable

import httpx
import pytest
from sfp_contracts.agents.readiness import ParsedTicket
from workspace_worker.repo.jira.client import (
    JiraClient,
    JiraClientError,
    JiraIssueResult,
    JiraTransitionResult,
    _redact,
)

SITE = "https://arconta.atlassian.net"
EMAIL = "bot@arconta.dev"
TOKEN = "ATATT3xFj777_secret_jira_api_token_value"
KEY = "SFP-225"
TRANSITION_ID = "41"

# Independent 8-header -> field oracle (not imported from the implementation).
SECTION_HEADERS: tuple[tuple[str, str], ...] = (
    ("Context", "context"),
    ("Requirements", "requirements"),
    ("Files to create/modify", "files_to_create_modify"),
    ("Implementation notes", "implementation_notes"),
    ("References", "references"),
    ("Context outputs / required inputs", "context_outputs_required_inputs"),
    ("Acceptance criteria", "acceptance_criteria"),
    ("Dependencies", "dependencies"),
)


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    """Build an httpx.Client that routes every request through ``handler``."""
    return httpx.Client(transport=httpx.MockTransport(handler))


def _text(text: str) -> dict[str, object]:
    return {"type": "text", "text": text}


def _full_issue_adf() -> dict[str, object]:
    """An ADF doc carrying all eight ID-070 sections (one heading + paragraph each)."""
    nodes: list[dict[str, object]] = []
    for header, field in SECTION_HEADERS:
        nodes.append({"type": "heading", "attrs": {"level": 2}, "content": [_text(header)]})
        nodes.append({"type": "paragraph", "content": [_text(f"<{field} body>")]})
    return {"type": "doc", "content": nodes}


def _issue_response(
    *,
    summary: str = "Implement the Jira client",
    description: dict[str, object] | None = None,
    status: str = "In Progress",
    labels: list[str] | None = None,
    key: str = KEY,
) -> dict[str, object]:
    desc = _full_issue_adf() if description is None else description
    return {
        "key": key,
        "fields": {
            "summary": summary,
            "description": desc,
            "status": {"name": status},
            "labels": ["a", "b"] if labels is None else labels,
        },
    }


# ---------------------------------------------------------------------------
# _redact helper (mirrors GitProviderAdapter redaction)
# ---------------------------------------------------------------------------


def test_redact_replaces_token() -> None:
    assert _redact(f"err {TOKEN} boom", TOKEN) == "err *** boom"


def test_redact_noop_for_empty_token() -> None:
    # A falsy token disables redaction — nothing to leak.
    assert _redact("some message", "") == "some message"


# ---------------------------------------------------------------------------
# result dataclasses — re-export + frozen-slots
# ---------------------------------------------------------------------------


def test_results_reexported_from_subpackage() -> None:
    from workspace_worker.repo import jira
    from workspace_worker.repo.jira import client, parser

    assert jira.JiraClient is client.JiraClient
    assert jira.JiraClientError is client.JiraClientError
    assert jira.JiraIssueResult is client.JiraIssueResult
    assert jira.JiraTransitionResult is client.JiraTransitionResult
    assert jira.adf_to_parsed_ticket is parser.adf_to_parsed_ticket
    for name in (
        "JiraClient",
        "JiraClientError",
        "JiraIssueResult",
        "JiraTransitionResult",
        "adf_to_parsed_ticket",
    ):
        assert name in jira.__all__


def test_jira_issue_result_frozen_slots() -> None:
    result = JiraIssueResult(
        key=KEY,
        summary="s",
        status="st",
        labels=("a", "b"),
        parsed=ParsedTicket(),
        raw_description=None,
    )
    fields = {f.name for f in dataclasses.fields(result)}
    assert fields == {"key", "summary", "status", "labels", "parsed", "raw_description"}
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.key = "x"  # type: ignore[misc]
    assert not hasattr(result, "__dict__")


def test_jira_transition_result_frozen_slots() -> None:
    result = JiraTransitionResult(key=KEY, transition_id=TRANSITION_ID)
    fields = {f.name for f in dataclasses.fields(result)}
    assert fields == {"key", "transition_id"}
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.key = "x"  # type: ignore[misc]
    assert not hasattr(result, "__dict__")


# ---------------------------------------------------------------------------
# Basic auth header — on EVERY request, and NOT Bearer
# ---------------------------------------------------------------------------


def test_default_client_carries_basic_header() -> None:
    # No client injected -> the default httpx.Client must carry the Basic header.
    jc = JiraClient(SITE, EMAIL, TOKEN)
    try:
        expected = "Basic " + base64.b64encode(f"{EMAIL}:{TOKEN}".encode()).decode()
        assert jc._client.headers.get("Authorization") == expected
    finally:
        jc._client.close()


def test_auth_is_basic_not_bearer() -> None:
    """(c) Auth header is 'Basic base64(email:token)' — decode the b64 to prove it;
    Bearer is NOT used."""
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization", "")
        return httpx.Response(204)

    jc = JiraClient(SITE, EMAIL, TOKEN, client=_client(handler), max_attempts=3)
    jc.transition(KEY, TRANSITION_ID)

    auth = captured["auth"]
    assert auth.startswith("Basic ")
    decoded = base64.b64decode(auth[len("Basic ") :]).decode()
    assert decoded == f"{EMAIL}:{TOKEN}"
    # Bearer is NOT used.
    assert not auth.startswith("Bearer ")


def test_basic_header_on_every_request() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("authorization", ""))
        return httpx.Response(200, json=_issue_response())

    jc = JiraClient(SITE, EMAIL, TOKEN, client=_client(handler), max_attempts=3)
    jc.fetch_issue(KEY)

    assert seen == [f"Basic {base64.b64encode(f'{EMAIL}:{TOKEN}'.encode()).decode()}"]


# ---------------------------------------------------------------------------
# fetch_issue — happy path (GET issue + ADF parse)
# ---------------------------------------------------------------------------


def test_fetch_issue_happy_path() -> None:
    """(a) GET /rest/api/3/issue/{key}?fields=... returns a JiraIssueResult whose
    parsed description is a ParsedTicket with the 8 sections populated."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.method == "GET"
        assert request.url.path == f"/rest/api/3/issue/{KEY}"
        assert "fields=summary,description,status,labels" in str(request.url)
        return httpx.Response(200, json=_issue_response())

    jc = JiraClient(SITE, EMAIL, TOKEN, client=_client(handler), max_attempts=3)
    result = jc.fetch_issue(KEY)

    assert result.key == KEY
    assert result.summary == "Implement the Jira client"
    assert result.status == "In Progress"
    assert result.labels == ("a", "b")
    assert isinstance(result.parsed, ParsedTicket)
    for _, field in SECTION_HEADERS:
        assert getattr(result.parsed, field) == f"<{field} body>"
    assert len(seen) == 1


def test_fetch_issue_description_none_yields_all_none_parsed() -> None:
    """A None description yields an all-None ParsedTicket (no raise)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "key": KEY,
                "fields": {
                    "summary": "no desc",
                    "description": None,
                    "status": {"name": "To Do"},
                    "labels": [],
                },
            },
        )

    jc = JiraClient(SITE, EMAIL, TOKEN, client=_client(handler), max_attempts=3)
    result = jc.fetch_issue(KEY)

    assert isinstance(result.parsed, ParsedTicket)
    for _, field in SECTION_HEADERS:
        assert getattr(result.parsed, field) is None
    assert result.raw_description is None
    assert result.labels == ()


def test_fetch_issue_fields_come_from_response_not_inputs() -> None:
    # Parse-not-echo: status/summary/labels are parsed from the response JSON.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_issue_response(summary="other summary", status="Done", labels=["x", "y", "z"]),
        )

    jc = JiraClient(SITE, EMAIL, TOKEN, client=_client(handler), max_attempts=3)
    result = jc.fetch_issue(KEY)

    assert result.summary == "other summary"
    assert result.status == "Done"
    assert result.labels == ("x", "y", "z")


# ---------------------------------------------------------------------------
# transition — happy path (POST transitions, 204 success)
# ---------------------------------------------------------------------------


def test_transition_happy_path_204() -> None:
    """(b) POST /rest/api/3/issue/{key}/transitions with the transition payload;
    204 -> JiraTransitionResult."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.method == "POST"
        assert request.url.path == f"/rest/api/3/issue/{KEY}/transitions"
        assert request.headers.get("authorization", "").startswith("Basic ")
        assert json.loads(request.content) == {"transition": {"id": TRANSITION_ID}}
        return httpx.Response(204)

    jc = JiraClient(SITE, EMAIL, TOKEN, client=_client(handler), max_attempts=3)
    result = jc.transition(KEY, TRANSITION_ID)

    assert result == JiraTransitionResult(key=KEY, transition_id=TRANSITION_ID)
    assert len(seen) == 1


# ---------------------------------------------------------------------------
# retry — then-succeed on transient failures (5xx, 429, network)
# ---------------------------------------------------------------------------


def test_transition_retry_then_succeed_on_503() -> None:
    """(d) A 503 is retried, then succeeds on the 3rd attempt."""
    tries = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal tries
        tries += 1
        if tries < 3:
            return httpx.Response(503, json={"message": "unavailable"})
        return httpx.Response(204)

    jc = JiraClient(SITE, EMAIL, TOKEN, client=_client(handler), max_attempts=3)
    jc.transition(KEY, TRANSITION_ID)

    assert tries == 3  # retried twice, succeeded on the 3rd attempt


def test_transition_retry_then_succeed_on_429() -> None:
    tries = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal tries
        tries += 1
        if tries < 3:
            return httpx.Response(429, json={"message": "rate limited"})
        return httpx.Response(204)

    jc = JiraClient(SITE, EMAIL, TOKEN, client=_client(handler), max_attempts=3)
    jc.transition(KEY, TRANSITION_ID)

    assert tries == 3


def test_transition_retry_then_succeed_on_connect_error() -> None:
    tries = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal tries
        tries += 1
        if tries < 3:
            raise httpx.ConnectError("connection refused")
        return httpx.Response(204)

    jc = JiraClient(SITE, EMAIL, TOKEN, client=_client(handler), max_attempts=3)
    jc.transition(KEY, TRANSITION_ID)

    assert tries == 3


# ---------------------------------------------------------------------------
# retry — exhaust (give-up) after the budget is spent
# ---------------------------------------------------------------------------


def test_transition_retry_exhaust_on_429_raises_jira_error() -> None:
    """(e) Persistent 429 exhausts the budget and surfaces a redacted
    JiraClientError (token absent from the message). Uses max_attempts=2 for speed."""
    tries = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal tries
        tries += 1
        return httpx.Response(429, json={"message": "rate limited"})

    jc = JiraClient(SITE, EMAIL, TOKEN, client=_client(handler), max_attempts=2)
    with pytest.raises(JiraClientError) as exc_info:
        jc.transition(KEY, TRANSITION_ID)

    msg = str(exc_info.value)
    assert "429" in msg
    assert "2 attempts" in msg
    assert TOKEN not in msg
    assert tries == 2  # exactly the budget


def test_transition_retry_exhaust_on_connect_error_raises_jira_error() -> None:
    tries = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal tries
        tries += 1
        raise httpx.ConnectError("connection refused")

    jc = JiraClient(SITE, EMAIL, TOKEN, client=_client(handler), max_attempts=2)
    with pytest.raises(JiraClientError, match="ConnectError") as exc_info:
        jc.transition(KEY, TRANSITION_ID)

    assert "2 attempts" in str(exc_info.value)
    assert TOKEN not in str(exc_info.value)
    assert tries == 2


# ---------------------------------------------------------------------------
# no retry — non-transient 4xx surfaces immediately
# ---------------------------------------------------------------------------


def test_fetch_issue_no_retry_on_404() -> None:
    """(f) A non-retryable 404 on fetch_issue surfaces a redacted JiraClientError."""
    tries = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal tries
        tries += 1
        return httpx.Response(404, json={"message": "Issue Does Not Exist"})

    jc = JiraClient(SITE, EMAIL, TOKEN, client=_client(handler), max_attempts=3)
    with pytest.raises(JiraClientError, match="404") as exc_info:
        jc.fetch_issue(KEY)

    assert TOKEN not in str(exc_info.value)
    assert tries == 1  # NOT retried


def test_transition_no_retry_on_400() -> None:
    tries = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal tries
        tries += 1
        return httpx.Response(400, json={"message": "bad transition id"})

    jc = JiraClient(SITE, EMAIL, TOKEN, client=_client(handler), max_attempts=3)
    with pytest.raises(JiraClientError, match="400"):
        jc.transition(KEY, "bogus")

    assert tries == 1  # NOT retried


# ---------------------------------------------------------------------------
# token redaction in surfaced errors
# ---------------------------------------------------------------------------


def test_token_redacted_from_error_body() -> None:
    # Pathological response body that happens to echo the token — the surfaced
    # error must redact it (defensive guarantee, mirrors GitProviderAdapter).
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text=f"forbidden: invalid token {TOKEN}")

    jc = JiraClient(SITE, EMAIL, TOKEN, client=_client(handler), max_attempts=3)
    with pytest.raises(JiraClientError) as exc_info:
        jc.fetch_issue(KEY)

    msg = str(exc_info.value)
    assert TOKEN not in msg
    assert "***" in msg


# ---------------------------------------------------------------------------
# local validation — ValueError before any HTTP call
# ---------------------------------------------------------------------------


def test_fetch_issue_empty_key_raises_before_any_http() -> None:
    """(g) Empty key raises ValueError before any HTTP call."""
    called: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        called.append(request)
        return httpx.Response(200, json=_issue_response())

    jc = JiraClient(SITE, EMAIL, TOKEN, client=_client(handler), max_attempts=3)
    with pytest.raises(ValueError, match="key"):
        jc.fetch_issue("")

    assert called == []  # no HTTP call made — validated locally


def test_transition_empty_key_raises_before_any_http() -> None:
    """(g) Empty key raises ValueError before any HTTP call."""
    called: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        called.append(request)
        return httpx.Response(204)

    jc = JiraClient(SITE, EMAIL, TOKEN, client=_client(handler), max_attempts=3)
    with pytest.raises(ValueError, match="key"):
        jc.transition("", TRANSITION_ID)

    assert called == []


def test_transition_empty_id_raises_before_any_http() -> None:
    """(g) Empty transition_id raises ValueError before any HTTP call."""
    called: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        called.append(request)
        return httpx.Response(204)

    jc = JiraClient(SITE, EMAIL, TOKEN, client=_client(handler), max_attempts=3)
    with pytest.raises(ValueError, match="transition_id"):
        jc.transition(KEY, "")

    assert called == []


# ---------------------------------------------------------------------------
# site trailing-slash normalization
# ---------------------------------------------------------------------------


def test_site_trailing_slash_normalized() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(204)

    jc = JiraClient(SITE + "/", EMAIL, TOKEN, client=_client(handler), max_attempts=3)
    jc.transition(KEY, TRANSITION_ID)

    # No double slash between the site and /rest/api/3.
    assert seen[0].startswith(f"{SITE}/rest/api/3/issue/{KEY}/transitions")
