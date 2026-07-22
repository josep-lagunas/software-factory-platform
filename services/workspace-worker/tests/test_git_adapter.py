"""Tests for :mod:`workspace_worker.repo.git.adapter` — the Git Provider push slice (SFP-58).

All tests inject an :class:`httpx.Client` built on :class:`httpx.MockTransport`,
so nothing ever touches ``api.github.com``. Each handler asserts the request
shape (method, URL path, bearer header, JSON payload) and returns canned
responses, including the retry-then-succeed and retry-exhaust paths.
"""

from __future__ import annotations

import dataclasses
import inspect
import json
from collections.abc import Callable

import httpx
import pytest
from workspace_worker.repo.git.adapter import (
    GitDeleteResult,
    GitProviderAdapter,
    GitProviderAdapterError,
    GitPushResult,
    GitSyncResult,
    PullRequestResult,
    ReviewResult,
    _redact,
)

TOKEN = "ghp_secrettoken_value_123"
OWNER = "arconta"
REPO = "sfp"
REF = "heads/sfp-58-git-adapter-push"
SHA = "0123456789abcdef0123456789abcdef01234567"

PR_NUMBER = 42
HEAD = "sfp-59-git-provider-adapter-pr"
BASE = "main"
TITLE = "SFP-59: PR create/update"
BODY = "JIRA: https://arconta.atlassian.net/browse/SFP-59"
PR_URL = f"https://github.com/{OWNER}/{REPO}/pull/{PR_NUMBER}"

# update-branch 202 response carries a `url` whose last path segment is the
# resulting sha; SYNC_SHA deliberately differs from the push SHA constant so
# parse-not-echo is observable.
SYNC_SHA = "fedcba9876543210fedcba9876543210fedcba98"
SYNC_URL = f"https://api.github.com/repos/{OWNER}/{REPO}/commits/{SYNC_SHA}"

# submit-review (SFP-60) — REVIEW_ID/REVIEW_STATE deliberately differ from any
# input so parse-not-echo is observable; REVIEW_EVENT is the GitHub event string
# a caller maps from a review-status enum (the adapter carries it verbatim).
REVIEW_ID = 9999
REVIEW_STATE = "APPROVED"
REVIEW_EVENT = "APPROVE"
REVIEW_BODY = "LGTM — looks good to merge."


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    """Build an httpx.Client that routes every request through ``handler``."""
    return httpx.Client(transport=httpx.MockTransport(handler))


def _refs_path() -> str:
    return f"/repos/{OWNER}/{REPO}/git/refs"


def _ref_path() -> str:
    return f"/repos/{OWNER}/{REPO}/git/refs/{REF}"


# ---------------------------------------------------------------------------
# _redact helper (mirrors RepoManager redaction)
# ---------------------------------------------------------------------------


def test_redact_replaces_token() -> None:
    assert _redact(f"err {TOKEN} boom", TOKEN) == "err *** boom"


def test_redact_noop_for_empty_token() -> None:
    # A falsy token disables redaction — nothing to leak (mirrors RepoManager).
    assert _redact("some message", "") == "some message"


# ---------------------------------------------------------------------------
# bearer auth header — present on EVERY request
# ---------------------------------------------------------------------------


def test_bearer_auth_header_on_every_request() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("authorization", ""))
        if request.method == "GET":
            return httpx.Response(404)  # ref absent -> POST create
        return httpx.Response(201)

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    adapter.push_branch(OWNER, REPO, REF, SHA)

    assert seen == [f"Bearer {TOKEN}", f"Bearer {TOKEN}"]


def test_default_client_carries_bearer_header() -> None:
    # No client injected -> the default httpx.Client must carry the bearer header.
    adapter = GitProviderAdapter(TOKEN)
    try:
        assert adapter._client.headers.get("Authorization") == f"Bearer {TOKEN}"
    finally:
        adapter._client.close()


# ---------------------------------------------------------------------------
# push_branch — create (POST) vs update (PATCH)
# ---------------------------------------------------------------------------


def test_push_branch_creates_when_ref_absent() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "GET":
            return httpx.Response(404, json={"message": "Not Found"})
        assert request.method == "POST"
        # Create payload carries both the ref and the sha.
        assert json.loads(request.content) == {"ref": REF, "sha": SHA}
        return httpx.Response(201, json={"ref": f"refs/{REF}", "object": {"sha": SHA}})

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    result = adapter.push_branch(OWNER, REPO, REF, SHA)

    assert result == GitPushResult(owner=OWNER, repo=REPO, ref=REF, sha=SHA, created=True)
    assert len(seen) == 2
    assert seen[0].method == "GET"
    assert seen[0].url.path == _ref_path()
    assert seen[1].method == "POST"
    assert seen[1].url.path == _refs_path()


def test_push_branch_updates_when_ref_exists() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"ref": f"refs/{REF}", "object": {"sha": "old"}})
        assert request.method == "PATCH"
        # Update payload carries only the new sha (the ref is in the URL).
        assert json.loads(request.content) == {"sha": SHA}
        return httpx.Response(200, json={"ref": f"refs/{REF}", "object": {"sha": SHA}})

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    result = adapter.push_branch(OWNER, REPO, REF, SHA)

    assert result == GitPushResult(owner=OWNER, repo=REPO, ref=REF, sha=SHA, created=False)
    assert len(seen) == 2
    assert seen[0].method == "GET"
    assert seen[1].method == "PATCH"
    assert seen[1].url.path == _ref_path()


# ---------------------------------------------------------------------------
# retry — then-succeed on transient failures (5xx, 429, network)
# ---------------------------------------------------------------------------


def test_retry_then_succeed_on_500() -> None:
    patch_tries = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal patch_tries
        if request.method == "GET":
            return httpx.Response(200)  # ref exists -> PATCH path
        patch_tries += 1
        if patch_tries < 3:
            return httpx.Response(500, json={"message": "server error"})
        return httpx.Response(200, json={"object": {"sha": SHA}})

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    result = adapter.push_branch(OWNER, REPO, REF, SHA)

    assert result.created is False
    assert patch_tries == 3  # retried twice, succeeded on the 3rd attempt


def test_retry_then_succeed_on_429() -> None:
    patch_tries = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal patch_tries
        if request.method == "GET":
            return httpx.Response(200)
        patch_tries += 1
        if patch_tries < 3:
            return httpx.Response(429, json={"message": "rate limited"})
        return httpx.Response(200, json={"object": {"sha": SHA}})

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    result = adapter.push_branch(OWNER, REPO, REF, SHA)

    assert result.created is False
    assert patch_tries == 3


def test_retry_then_succeed_on_connect_error() -> None:
    patch_tries = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal patch_tries
        if request.method == "GET":
            return httpx.Response(200)
        patch_tries += 1
        if patch_tries < 3:
            raise httpx.ConnectError("connection refused")
        return httpx.Response(200, json={"object": {"sha": SHA}})

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    result = adapter.push_branch(OWNER, REPO, REF, SHA)

    assert result.created is False
    assert patch_tries == 3


# ---------------------------------------------------------------------------
# retry — exhaust (give-up) after the budget is spent
# ---------------------------------------------------------------------------


def test_retry_exhaust_on_503_raises_adapter_error() -> None:
    patch_tries = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal patch_tries
        if request.method == "GET":
            return httpx.Response(200)  # exists -> PATCH path
        patch_tries += 1
        return httpx.Response(503, json={"message": "unavailable"})

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    with pytest.raises(GitProviderAdapterError) as exc_info:
        adapter.push_branch(OWNER, REPO, REF, SHA)

    msg = str(exc_info.value)
    assert "503" in msg
    assert "3 attempts" in msg
    assert TOKEN not in msg
    assert patch_tries == 3  # exactly the budget — no more, no fewer


def test_retry_exhaust_on_connect_error_raises_adapter_error() -> None:
    patch_tries = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal patch_tries
        if request.method == "GET":
            return httpx.Response(200)
        patch_tries += 1
        raise httpx.ConnectError("connection refused")

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    with pytest.raises(GitProviderAdapterError, match="ConnectError") as exc_info:
        adapter.push_branch(OWNER, REPO, REF, SHA)

    assert "3 attempts" in str(exc_info.value)
    assert patch_tries == 3


# ---------------------------------------------------------------------------
# no retry — non-transient 4xx surfaces immediately
# ---------------------------------------------------------------------------


def test_no_retry_on_422() -> None:
    patch_tries = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal patch_tries
        if request.method == "GET":
            return httpx.Response(200)  # exists -> PATCH path
        patch_tries += 1
        return httpx.Response(422, json={"message": "Validation Failed"})

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    with pytest.raises(GitProviderAdapterError, match="422"):
        adapter.push_branch(OWNER, REPO, REF, SHA)

    assert patch_tries == 1  # NOT retried


def test_no_retry_on_probe_403() -> None:
    get_tries = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal get_tries
        # The probe is a non-retryable 4xx that is not the 404 create-signal.
        if request.method == "GET":
            get_tries += 1
            return httpx.Response(403, json={"message": "Forbidden"})
        raise AssertionError("no push should happen after a forbidden probe")

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    with pytest.raises(GitProviderAdapterError, match="403"):
        adapter.push_branch(OWNER, REPO, REF, SHA)

    assert get_tries == 1  # the probe failed and surfaced immediately


# ---------------------------------------------------------------------------
# token redaction in surfaced errors
# ---------------------------------------------------------------------------


def test_token_redacted_from_error_body() -> None:
    # Pathological response body that happens to echo the token — the surfaced
    # error must redact it (defensive guarantee, mirrors RepoManager).
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text=f"forbidden: invalid token {TOKEN}")

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    with pytest.raises(GitProviderAdapterError) as exc_info:
        adapter.push_branch(OWNER, REPO, REF, SHA)

    msg = str(exc_info.value)
    assert TOKEN not in msg
    assert "***" in msg


# ---------------------------------------------------------------------------
# open questions — empty ref / empty token
# ---------------------------------------------------------------------------


def test_empty_ref_raises_value_error_before_any_http() -> None:
    called: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        called.append(request)
        return httpx.Response(404)

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    with pytest.raises(ValueError, match="ref"):
        adapter.push_branch(OWNER, REPO, "", SHA)

    assert called == []  # no HTTP call made — validated locally


# ---------------------------------------------------------------------------
# delete_ref — DELETE a remote ref (SFP-57)
# ---------------------------------------------------------------------------


def test_git_delete_result_reexported() -> None:
    # AC10: GitDeleteResult is re-exported from the git subpackage.
    from workspace_worker.repo.git import GitDeleteResult as Reexported

    assert Reexported is GitDeleteResult


def test_delete_ref_issues_delete_with_bearer_header() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.method == "DELETE"
        assert request.url.path == _ref_path()
        assert request.headers.get("authorization") == f"Bearer {TOKEN}"
        # No JSON body for DELETE.
        assert request.content in (b"", b"null")
        return httpx.Response(204)

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    result = adapter.delete_ref(OWNER, REPO, REF)

    assert result == GitDeleteResult(owner=OWNER, repo=REPO, ref=REF)
    assert len(seen) == 1


def test_delete_ref_success_on_200() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    result = adapter.delete_ref(OWNER, REPO, REF)

    assert result == GitDeleteResult(owner=OWNER, repo=REPO, ref=REF)


def test_delete_ref_retry_then_succeed_on_500() -> None:
    tries = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal tries
        tries += 1
        if tries < 3:
            return httpx.Response(500, json={"message": "server error"})
        return httpx.Response(204)

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    adapter.delete_ref(OWNER, REPO, REF)

    assert tries == 3  # retried twice, succeeded on the 3rd attempt


def test_delete_ref_retry_then_succeed_on_429() -> None:
    tries = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal tries
        tries += 1
        if tries < 3:
            return httpx.Response(429, json={"message": "rate limited"})
        return httpx.Response(204)

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    adapter.delete_ref(OWNER, REPO, REF)

    assert tries == 3


def test_delete_ref_retry_then_succeed_on_connect_error() -> None:
    tries = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal tries
        tries += 1
        if tries < 3:
            raise httpx.ConnectError("connection refused")
        return httpx.Response(204)

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    adapter.delete_ref(OWNER, REPO, REF)

    assert tries == 3


def test_delete_ref_retry_exhaust_on_503_raises_adapter_error() -> None:
    tries = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal tries
        tries += 1
        return httpx.Response(503, json={"message": "unavailable"})

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    with pytest.raises(GitProviderAdapterError) as exc_info:
        adapter.delete_ref(OWNER, REPO, REF)

    msg = str(exc_info.value)
    assert "503" in msg
    assert "3 attempts" in msg
    assert TOKEN not in msg
    assert tries == 3  # exactly the budget


def test_delete_ref_no_retry_on_422() -> None:
    tries = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal tries
        tries += 1
        return httpx.Response(422, json={"message": "Validation Failed"})

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    with pytest.raises(GitProviderAdapterError, match="422"):
        adapter.delete_ref(OWNER, REPO, REF)

    assert tries == 1  # NOT retried


def test_delete_ref_no_retry_on_404() -> None:
    # AC9 / R4: a 404 (ref already gone) is NOT retried and NOT swallowed — it
    # surfaces as a redacted GitProviderAdapterError. End-to-end remote-delete
    # idempotency on an already-gone ref is an explicit out-of-scope refinement.
    tries = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal tries
        tries += 1
        return httpx.Response(404, json={"message": "Not Found"})

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    with pytest.raises(GitProviderAdapterError, match="404"):
        adapter.delete_ref(OWNER, REPO, REF)

    assert tries == 1  # NOT retried


def test_delete_ref_token_redacted_from_error_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text=f"forbidden: invalid token {TOKEN}")

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    with pytest.raises(GitProviderAdapterError) as exc_info:
        adapter.delete_ref(OWNER, REPO, REF)

    msg = str(exc_info.value)
    assert TOKEN not in msg
    assert "***" in msg


def test_delete_ref_empty_ref_raises_value_error_before_any_http() -> None:
    called: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        called.append(request)
        return httpx.Response(204)

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    with pytest.raises(ValueError, match="ref"):
        adapter.delete_ref(OWNER, REPO, "")

    assert called == []  # no HTTP call made — validated locally


def test_empty_token_accepted_redaction_noop() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        # Empty token -> "Bearer " header; accepted, redaction is a no-op.
        assert request.headers.get("authorization") == "Bearer "
        if request.method == "GET":
            return httpx.Response(404)
        return httpx.Response(201)

    adapter = GitProviderAdapter("", client=_client(handler), max_attempts=3)
    result = adapter.push_branch(OWNER, REPO, REF, SHA)

    assert result.created is True


# ---------------------------------------------------------------------------
# PullRequestResult — re-export + frozen-slots (SFP-59)
# ---------------------------------------------------------------------------


def test_pull_request_result_reexported() -> None:
    # AC8: PullRequestResult is re-exported from the git subpackage.
    from workspace_worker.repo import git
    from workspace_worker.repo.git.adapter import PullRequestResult as Direct

    assert git.PullRequestResult is Direct
    assert "PullRequestResult" in git.__all__


def test_pull_request_result_frozen_slots() -> None:
    # AC7: @dataclass(frozen=True, slots=True) with (owner, repo, number, url, state).
    result = PullRequestResult(owner=OWNER, repo=REPO, number=PR_NUMBER, url=PR_URL, state="open")
    fields = {f.name for f in dataclasses.fields(result)}
    assert fields == {"owner", "repo", "number", "url", "state"}
    # frozen — assignment raises FrozenInstanceError
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.number = 99  # type: ignore[misc]
    # slots — no per-instance __dict__
    assert not hasattr(result, "__dict__")


# ---------------------------------------------------------------------------
# create_pr — request shape (POST /repos/{owner}/{repo}/pulls, bearer, JSON)
# ---------------------------------------------------------------------------


def _pr_response(
    *, number: int = PR_NUMBER, url: str = PR_URL, state: str = "open"
) -> dict[str, object]:
    return {"number": number, "html_url": url, "state": state}


def test_create_pr_issues_post_with_bearer_header() -> None:
    # AC1: POST /repos/{owner}/{repo}/pulls with {title,head,base,body} + bearer.
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.method == "POST"
        assert request.url.path == f"/repos/{OWNER}/{REPO}/pulls"
        assert request.headers.get("authorization") == f"Bearer {TOKEN}"
        assert json.loads(request.content) == {
            "title": TITLE,
            "head": HEAD,
            "base": BASE,
            "body": BODY,
        }
        return httpx.Response(201, json=_pr_response())

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    result = adapter.create_pr(OWNER, REPO, title=TITLE, head=HEAD, base=BASE, body=BODY)

    assert result == PullRequestResult(
        owner=OWNER, repo=REPO, number=PR_NUMBER, url=PR_URL, state="open"
    )
    assert len(seen) == 1


def test_create_pr_result_fields_come_from_response() -> None:
    # AC2: the result number/url/state are parsed from the response JSON, not
    # inferred from the inputs (the response fields deliberately differ).
    resp = _pr_response(number=77, url="https://github.com/different/repo/pull/77", state="closed")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json=resp)

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    result = adapter.create_pr(OWNER, REPO, title=TITLE, head=HEAD, base=BASE, body=BODY)

    assert result.number == 77
    assert result.url == "https://github.com/different/repo/pull/77"
    assert result.state == "closed"


# ---------------------------------------------------------------------------
# create_pr — retry-then-succeed on transient failures
# ---------------------------------------------------------------------------


def test_create_pr_retry_then_succeed_on_500() -> None:
    tries = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal tries
        tries += 1
        if tries < 3:
            return httpx.Response(500, json={"message": "server error"})
        return httpx.Response(201, json=_pr_response())

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    adapter.create_pr(OWNER, REPO, title=TITLE, head=HEAD, base=BASE, body=BODY)

    assert tries == 3  # retried twice, succeeded on the 3rd attempt


def test_create_pr_retry_then_succeed_on_429() -> None:
    tries = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal tries
        tries += 1
        if tries < 3:
            return httpx.Response(429, json={"message": "rate limited"})
        return httpx.Response(201, json=_pr_response())

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    adapter.create_pr(OWNER, REPO, title=TITLE, head=HEAD, base=BASE, body=BODY)

    assert tries == 3


def test_create_pr_retry_then_succeed_on_connect_error() -> None:
    tries = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal tries
        tries += 1
        if tries < 3:
            raise httpx.ConnectError("connection refused")
        return httpx.Response(201, json=_pr_response())

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    adapter.create_pr(OWNER, REPO, title=TITLE, head=HEAD, base=BASE, body=BODY)

    assert tries == 3


# ---------------------------------------------------------------------------
# create_pr — retry-exhaust surfaces a redacted error
# ---------------------------------------------------------------------------


def test_create_pr_retry_exhaust_on_503_raises_adapter_error() -> None:
    tries = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal tries
        tries += 1
        return httpx.Response(503, json={"message": "unavailable"})

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    with pytest.raises(GitProviderAdapterError) as exc_info:
        adapter.create_pr(OWNER, REPO, title=TITLE, head=HEAD, base=BASE, body=BODY)

    msg = str(exc_info.value)
    assert "503" in msg
    assert "3 attempts" in msg
    assert TOKEN not in msg
    assert tries == 3  # exactly the budget


# ---------------------------------------------------------------------------
# create_pr — no retry on non-transient 4xx
# ---------------------------------------------------------------------------


def test_create_pr_no_retry_on_422() -> None:
    tries = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal tries
        tries += 1
        return httpx.Response(422, json={"message": "Validation Failed"})

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    with pytest.raises(GitProviderAdapterError, match="422"):
        adapter.create_pr(OWNER, REPO, title=TITLE, head=HEAD, base=BASE, body=BODY)

    assert tries == 1  # NOT retried


def test_create_pr_no_retry_on_403() -> None:
    tries = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal tries
        tries += 1
        return httpx.Response(403, json={"message": "Forbidden"})

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    with pytest.raises(GitProviderAdapterError, match="403"):
        adapter.create_pr(OWNER, REPO, title=TITLE, head=HEAD, base=BASE, body=BODY)

    assert tries == 1  # NOT retried


# ---------------------------------------------------------------------------
# create_pr — token redaction + local validation
# ---------------------------------------------------------------------------


def test_create_pr_token_redacted_from_error_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text=f"forbidden: invalid token {TOKEN}")

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    with pytest.raises(GitProviderAdapterError) as exc_info:
        adapter.create_pr(OWNER, REPO, title=TITLE, head=HEAD, base=BASE, body=BODY)

    msg = str(exc_info.value)
    assert TOKEN not in msg
    assert "***" in msg


@pytest.mark.parametrize(
    "kwargs,field",
    [
        ({"title": "", "head": HEAD, "base": BASE, "body": BODY}, "title"),
        ({"title": TITLE, "head": "", "base": BASE, "body": BODY}, "head"),
        ({"title": TITLE, "head": HEAD, "base": "", "body": BODY}, "base"),
    ],
)
def test_create_pr_empty_arg_raises_value_error_before_any_http(
    kwargs: dict[str, str], field: str
) -> None:
    called: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        called.append(request)
        return httpx.Response(201, json=_pr_response())

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    with pytest.raises(ValueError, match=field):
        adapter.create_pr(OWNER, REPO, **kwargs)

    assert called == []  # no HTTP call made — validated locally


@pytest.mark.parametrize("which", ["owner", "repo"])
def test_create_pr_empty_owner_or_repo_raises_value_error_before_any_http(
    which: str,
) -> None:
    # AC10: empty owner/repo raises ValueError before any HTTP. Mirrors
    # push_branch/delete_ref's falsy guard (``if not owner``).
    called: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        called.append(request)
        return httpx.Response(201, json=_pr_response())

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    if which == "owner":
        with pytest.raises(ValueError, match="owner"):
            adapter.create_pr("", REPO, title=TITLE, head=HEAD, base=BASE, body=BODY)
    else:
        with pytest.raises(ValueError, match="repo"):
            adapter.create_pr(OWNER, "", title=TITLE, head=HEAD, base=BASE, body=BODY)

    assert called == []  # no HTTP call made — validated locally


# ---------------------------------------------------------------------------
# update_pr — request shape (PATCH, non-None-only payload)
# ---------------------------------------------------------------------------


def test_update_pr_issues_patch_with_only_non_none_fields() -> None:
    # AC3: PATCH /repos/{owner}/{repo}/pulls/{number} with ONLY the non-None
    # fields; None fields are omitted (not serialized as null).
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.method == "PATCH"
        assert request.url.path == f"/repos/{OWNER}/{REPO}/pulls/{PR_NUMBER}"
        assert request.headers.get("authorization") == f"Bearer {TOKEN}"
        assert json.loads(request.content) == {"title": TITLE}
        return httpx.Response(200, json=_pr_response(state="open"))

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    result = adapter.update_pr(OWNER, REPO, PR_NUMBER, title=TITLE)

    assert result == PullRequestResult(
        owner=OWNER, repo=REPO, number=PR_NUMBER, url=PR_URL, state="open"
    )
    assert len(seen) == 1


def test_update_pr_patch_payload_omits_none_fields() -> None:
    # When title is None, the payload must be exactly {body, state} — no null.
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(200, json=_pr_response(state="closed"))

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    result = adapter.update_pr(OWNER, REPO, PR_NUMBER, title=None, body=BODY, state="closed")

    assert captured["payload"] == {"body": BODY, "state": "closed"}
    assert result.state == "closed"


def test_update_pr_all_three_fields() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(200, json=_pr_response())

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    adapter.update_pr(OWNER, REPO, PR_NUMBER, title=TITLE, body=BODY, state="open")

    assert captured["payload"] == {"title": TITLE, "body": BODY, "state": "open"}


# ---------------------------------------------------------------------------
# update_pr — empty-payload guard, invalid number, local validation
# ---------------------------------------------------------------------------


def test_update_pr_empty_payload_raises_value_error_before_any_http() -> None:
    # AC4: title=body=state=None raises ValueError BEFORE any HTTP call.
    called: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        called.append(request)
        return httpx.Response(200, json=_pr_response())

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    with pytest.raises(ValueError, match="at least one"):
        adapter.update_pr(OWNER, REPO, PR_NUMBER)

    assert called == []  # no HTTP call made — validated locally


def test_update_pr_invalid_number_raises_value_error_before_any_http() -> None:
    # AC10: number < 1 raises ValueError before any HTTP.
    called: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        called.append(request)
        return httpx.Response(200, json=_pr_response())

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    with pytest.raises(ValueError, match="number"):
        adapter.update_pr(OWNER, REPO, 0, title=TITLE)

    assert called == []


def test_update_pr_empty_owner_or_repo_raises_value_error_before_any_http() -> None:
    called: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        called.append(request)
        return httpx.Response(200, json=_pr_response())

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    with pytest.raises(ValueError, match="owner"):
        adapter.update_pr("", REPO, PR_NUMBER, title=TITLE)
    with pytest.raises(ValueError, match="repo"):
        adapter.update_pr(OWNER, "", PR_NUMBER, title=TITLE)

    assert called == []


# ---------------------------------------------------------------------------
# update_pr — retry, no-retry 4xx, token redaction
# ---------------------------------------------------------------------------


def test_update_pr_retry_then_succeed_on_500() -> None:
    tries = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal tries
        tries += 1
        if tries < 3:
            return httpx.Response(500, json={"message": "server error"})
        return httpx.Response(200, json=_pr_response())

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    adapter.update_pr(OWNER, REPO, PR_NUMBER, title=TITLE)

    assert tries == 3


def test_update_pr_no_retry_on_404() -> None:
    tries = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal tries
        tries += 1
        return httpx.Response(404, json={"message": "Not Found"})

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    with pytest.raises(GitProviderAdapterError, match="404"):
        adapter.update_pr(OWNER, REPO, PR_NUMBER, title=TITLE)

    assert tries == 1  # NOT retried


def test_update_pr_token_redacted_from_error_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text=f"forbidden: invalid token {TOKEN}")

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    with pytest.raises(GitProviderAdapterError) as exc_info:
        adapter.update_pr(OWNER, REPO, PR_NUMBER, title=TITLE)

    msg = str(exc_info.value)
    assert TOKEN not in msg
    assert "***" in msg


# ---------------------------------------------------------------------------
# format-agnostic — title/body pass through verbatim (AC9)
# ---------------------------------------------------------------------------


def test_create_pr_title_body_pass_verbatim() -> None:
    # AC9: the adapter is format-agnostic — title/body are not mutated. A title
    # and body with no SFP/JIRA framing arrive byte-for-byte at the API.
    raw_title = "just a plain title with no framing"
    raw_body = "a body\nwith newlines\nand weird chars: ~!@#$%^&*()"
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        captured["title"] = payload["title"]
        captured["body"] = payload["body"]
        return httpx.Response(201, json=_pr_response())

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    adapter.create_pr(OWNER, REPO, title=raw_title, head=HEAD, base=BASE, body=raw_body)

    assert captured["title"] == raw_title
    assert captured["body"] == raw_body


def test_adapter_methods_have_no_title_body_formatting_logic() -> None:
    # AC9 (structural): the create_pr/update_pr method bodies contain no string
    # mutation of title/body — they are passed straight into the payload dict.
    # A literal source scan for "SFP-"/"JIRA" is intentionally NOT used here, as
    # the module header carries provenance ticket references (SFP-58/38/28);
    # instead we assert the method source performs no title/body concatenation.
    import workspace_worker.repo.git.adapter as adapter_mod

    create_src = inspect.getsource(adapter_mod.GitProviderAdapter.create_pr)
    update_src = inspect.getsource(adapter_mod.GitProviderAdapter.update_pr)
    # No f-string/concatenation that would prefix or rewrite title/body.
    for src in (create_src, update_src):
        assert "title +" not in src
        assert "+ title" not in src
        assert "body +" not in src
        assert "+ body" not in src
        assert ".format(" not in src
        # title/body are only referenced as plain identifiers (passed through).
        assert "SFP-" not in src
        assert "JIRA" not in src


# ---------------------------------------------------------------------------
# sync_branch — update-branch (SFP-44)
# ---------------------------------------------------------------------------


def test_git_sync_result_reexported() -> None:
    # GitSyncResult is re-exported from the git subpackage.
    from workspace_worker.repo import git
    from workspace_worker.repo.git.adapter import GitSyncResult as Direct

    assert git.GitSyncResult is Direct
    assert "GitSyncResult" in git.__all__


def test_git_sync_result_frozen_slots() -> None:
    result = GitSyncResult(owner=OWNER, repo=REPO, pull_number=PR_NUMBER, sha=SYNC_SHA)
    fields = {f.name for f in dataclasses.fields(result)}
    assert fields == {"owner", "repo", "pull_number", "sha"}
    # frozen — assignment raises FrozenInstanceError
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.sha = "other"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.pull_number = 99  # type: ignore[misc]
    # slots — no per-instance __dict__
    assert not hasattr(result, "__dict__")


def test_sync_branch_issues_put_with_bearer_header() -> None:
    # PUT /repos/{owner}/{repo}/pulls/{pull_number}/update-branch, bearer header,
    # empty body; returns 202 with url.
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.method == "PUT"
        assert request.url.path == f"/repos/{OWNER}/{REPO}/pulls/{PR_NUMBER}/update-branch"
        assert request.headers.get("authorization") == f"Bearer {TOKEN}"
        # update-branch takes no JSON payload — body is empty.
        assert request.content in (b"", b"null")
        return httpx.Response(202, json={"url": SYNC_URL})

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    result = adapter.sync_branch(OWNER, REPO, PR_NUMBER)

    assert result == GitSyncResult(owner=OWNER, repo=REPO, pull_number=PR_NUMBER, sha=SYNC_SHA)
    assert len(seen) == 1


def test_sync_branch_extracts_sha_from_url_field() -> None:
    # The sha is parsed from the LAST path segment of the response url —
    # sync_branch takes no sha argument, so this proves parse-not-echo. The last
    # segment deliberately differs from the push-test SHA constant.
    resp_url = f"https://api.github.com/repos/{OWNER}/{REPO}/commits/{SYNC_SHA}"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(202, json={"url": resp_url})

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    result = adapter.sync_branch(OWNER, REPO, PR_NUMBER)

    assert result.sha == SYNC_SHA
    assert result.sha != SHA  # parsed from response, not the push-test constant


def test_sync_branch_empty_sha_when_url_absent() -> None:
    # No `url` key in the 202 response -> sha degrades to "" (graceful, no raise).
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(202, json={"message": "Updating pull request branch."})

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    result = adapter.sync_branch(OWNER, REPO, PR_NUMBER)

    assert result.sha == ""


def test_sync_branch_empty_sha_when_url_unparseable() -> None:
    # Validator correction #2: a `url` that cannot be parsed (here a non-string)
    # degrades to "" via the defensive guard — graceful, never raises.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(202, json={"url": 12345})

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    result = adapter.sync_branch(OWNER, REPO, PR_NUMBER)

    assert result.sha == ""


def test_sync_branch_success_on_202() -> None:
    # 202 Accepted is a success (is_success covers all 2xx) — no poll, no raise.
    tries = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal tries
        tries += 1
        return httpx.Response(202, json={"url": SYNC_URL})

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    result = adapter.sync_branch(OWNER, REPO, PR_NUMBER)

    assert tries == 1  # success on the first try — no polling loop
    assert result.pull_number == PR_NUMBER


# ---------------------------------------------------------------------------
# sync_branch — retry-then-succeed on transient failures
# ---------------------------------------------------------------------------


def test_sync_branch_retry_then_succeed_on_500() -> None:
    tries = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal tries
        tries += 1
        if tries < 3:
            return httpx.Response(500, json={"message": "server error"})
        return httpx.Response(202, json={"url": SYNC_URL})

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    adapter.sync_branch(OWNER, REPO, PR_NUMBER)

    assert tries == 3  # retried twice, succeeded on the 3rd attempt


def test_sync_branch_retry_then_succeed_on_429() -> None:
    tries = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal tries
        tries += 1
        if tries < 3:
            return httpx.Response(429, json={"message": "rate limited"})
        return httpx.Response(202, json={"url": SYNC_URL})

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    adapter.sync_branch(OWNER, REPO, PR_NUMBER)

    assert tries == 3


def test_sync_branch_retry_then_succeed_on_connect_error() -> None:
    tries = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal tries
        tries += 1
        if tries < 3:
            raise httpx.ConnectError("connection refused")
        return httpx.Response(202, json={"url": SYNC_URL})

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    adapter.sync_branch(OWNER, REPO, PR_NUMBER)

    assert tries == 3


# ---------------------------------------------------------------------------
# sync_branch — retry-exhaust surfaces a redacted error
# ---------------------------------------------------------------------------


def test_sync_branch_retry_exhaust_on_503_raises_adapter_error() -> None:
    tries = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal tries
        tries += 1
        return httpx.Response(503, json={"message": "unavailable"})

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    with pytest.raises(GitProviderAdapterError) as exc_info:
        adapter.sync_branch(OWNER, REPO, PR_NUMBER)

    msg = str(exc_info.value)
    assert "503" in msg
    assert "3 attempts" in msg
    assert TOKEN not in msg
    assert tries == 3  # exactly the budget


# ---------------------------------------------------------------------------
# sync_branch — 409 conflict is a NORMAL FAILURE, not blocked (ID-068)
# ---------------------------------------------------------------------------


def test_sync_branch_409_conflict_is_normal_failure_not_blocked() -> None:
    # ID-068: a 409 Conflict (merge conflict) is a NORMAL FAILURE, not a blocked
    # state. It is NOT retried (409 is not in _RETRY_STATUSES) and surfaces as a
    # plain redacted GitProviderAdapterError — there is NO separate "blocked"
    # exception type.
    tries = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal tries
        tries += 1
        return httpx.Response(
            409, json={"message": "merge conflict: head branch is not behind base"}
        )

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    with pytest.raises(GitProviderAdapterError, match="409") as exc_info:
        adapter.sync_branch(OWNER, REPO, PR_NUMBER)

    assert tries == 1  # NOT retried — 409 is not a transient status
    # Exact type — no subclass, no separate "blocked" exception type.
    assert type(exc_info.value) is GitProviderAdapterError
    assert TOKEN not in str(exc_info.value)


# ---------------------------------------------------------------------------
# sync_branch — no retry on non-transient 4xx
# ---------------------------------------------------------------------------


def test_sync_branch_no_retry_on_422() -> None:
    tries = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal tries
        tries += 1
        return httpx.Response(422, json={"message": "Validation Failed"})

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    with pytest.raises(GitProviderAdapterError, match="422"):
        adapter.sync_branch(OWNER, REPO, PR_NUMBER)

    assert tries == 1  # NOT retried


def test_sync_branch_no_retry_on_403() -> None:
    tries = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal tries
        tries += 1
        return httpx.Response(403, json={"message": "Forbidden"})

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    with pytest.raises(GitProviderAdapterError, match="403"):
        adapter.sync_branch(OWNER, REPO, PR_NUMBER)

    assert tries == 1  # NOT retried


def test_sync_branch_no_retry_on_404() -> None:
    tries = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal tries
        tries += 1
        return httpx.Response(404, json={"message": "Not Found"})

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    with pytest.raises(GitProviderAdapterError, match="404"):
        adapter.sync_branch(OWNER, REPO, PR_NUMBER)

    assert tries == 1  # NOT retried


# ---------------------------------------------------------------------------
# sync_branch — token redaction + local validation
# ---------------------------------------------------------------------------


def test_sync_branch_token_redacted_from_error_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text=f"forbidden: invalid token {TOKEN}")

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    with pytest.raises(GitProviderAdapterError) as exc_info:
        adapter.sync_branch(OWNER, REPO, PR_NUMBER)

    msg = str(exc_info.value)
    assert TOKEN not in msg
    assert "***" in msg


@pytest.mark.parametrize("which", ["owner", "repo"])
def test_sync_branch_empty_owner_or_repo_raises_value_error_before_any_http(
    which: str,
) -> None:
    called: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        called.append(request)
        return httpx.Response(202, json={"url": SYNC_URL})

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    if which == "owner":
        with pytest.raises(ValueError, match="owner"):
            adapter.sync_branch("", REPO, PR_NUMBER)
    else:
        with pytest.raises(ValueError, match="repo"):
            adapter.sync_branch(OWNER, "", PR_NUMBER)

    assert called == []  # no HTTP call made — validated locally


def test_sync_branch_invalid_pull_number_raises_value_error_before_any_http() -> None:
    called: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        called.append(request)
        return httpx.Response(202, json={"url": SYNC_URL})

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    with pytest.raises(ValueError, match="pull_number"):
        adapter.sync_branch(OWNER, REPO, 0)

    assert called == []  # no HTTP call made — validated locally


# ---------------------------------------------------------------------------
# ReviewResult — re-export + frozen-slots (SFP-60)
# ---------------------------------------------------------------------------


def test_review_result_reexported() -> None:
    # ReviewResult is re-exported from the git subpackage.
    from workspace_worker.repo import git
    from workspace_worker.repo.git.adapter import ReviewResult as Direct

    assert git.ReviewResult is Direct
    assert "ReviewResult" in git.__all__


def test_review_result_frozen_slots() -> None:
    result = ReviewResult(
        owner=OWNER, repo=REPO, number=PR_NUMBER, review_id=REVIEW_ID, state=REVIEW_STATE
    )
    fields = {f.name for f in dataclasses.fields(result)}
    assert fields == {"owner", "repo", "number", "review_id", "state"}
    # frozen — assignment raises FrozenInstanceError
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.review_id = 1  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.state = "x"  # type: ignore[misc]
    # slots — no per-instance __dict__
    assert not hasattr(result, "__dict__")


# ---------------------------------------------------------------------------
# submit_review — request shape (POST /pulls/{n}/reviews, bearer, JSON)
# ---------------------------------------------------------------------------


def _review_response(*, review_id: int = REVIEW_ID, state: str = REVIEW_STATE) -> dict[str, object]:
    return {"id": review_id, "state": state}


def test_submit_review_issues_post_with_bearer_header() -> None:
    # POST /repos/{owner}/{repo}/pulls/{number}/reviews with {event, body} +
    # bearer; result fields parsed from the response.
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.method == "POST"
        assert request.url.path == f"/repos/{OWNER}/{REPO}/pulls/{PR_NUMBER}/reviews"
        assert request.headers.get("authorization") == f"Bearer {TOKEN}"
        assert json.loads(request.content) == {"event": REVIEW_EVENT, "body": REVIEW_BODY}
        return httpx.Response(200, json=_review_response())

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    result = adapter.submit_review(OWNER, REPO, PR_NUMBER, event=REVIEW_EVENT, body=REVIEW_BODY)

    assert result == ReviewResult(
        owner=OWNER, repo=REPO, number=PR_NUMBER, review_id=REVIEW_ID, state=REVIEW_STATE
    )
    assert len(seen) == 1


def test_submit_review_result_fields_come_from_response() -> None:
    # Parse-not-echo: review_id/state come from the response JSON, not inferred
    # from the inputs (the response fields deliberately differ).
    resp = _review_response(review_id=12345, state="CHANGES_REQUESTED")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=resp)

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    result = adapter.submit_review(OWNER, REPO, PR_NUMBER, event=REVIEW_EVENT, body=REVIEW_BODY)

    assert result.review_id == 12345
    assert result.state == "CHANGES_REQUESTED"


# ---------------------------------------------------------------------------
# submit_review — retry-then-succeed on transient failures
# ---------------------------------------------------------------------------


def test_submit_review_retry_then_succeed_on_500() -> None:
    tries = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal tries
        tries += 1
        if tries < 3:
            return httpx.Response(500, json={"message": "server error"})
        return httpx.Response(200, json=_review_response())

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    adapter.submit_review(OWNER, REPO, PR_NUMBER, event=REVIEW_EVENT, body=REVIEW_BODY)

    assert tries == 3  # retried twice, succeeded on the 3rd attempt


def test_submit_review_retry_then_succeed_on_429() -> None:
    tries = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal tries
        tries += 1
        if tries < 3:
            return httpx.Response(429, json={"message": "rate limited"})
        return httpx.Response(200, json=_review_response())

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    adapter.submit_review(OWNER, REPO, PR_NUMBER, event=REVIEW_EVENT, body=REVIEW_BODY)

    assert tries == 3


def test_submit_review_retry_then_succeed_on_connect_error() -> None:
    tries = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal tries
        tries += 1
        if tries < 3:
            raise httpx.ConnectError("connection refused")
        return httpx.Response(200, json=_review_response())

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    adapter.submit_review(OWNER, REPO, PR_NUMBER, event=REVIEW_EVENT, body=REVIEW_BODY)

    assert tries == 3


# ---------------------------------------------------------------------------
# submit_review — retry-exhaust surfaces a redacted error
# ---------------------------------------------------------------------------


def test_submit_review_retry_exhaust_on_503_raises_adapter_error() -> None:
    tries = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal tries
        tries += 1
        return httpx.Response(503, json={"message": "unavailable"})

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    with pytest.raises(GitProviderAdapterError) as exc_info:
        adapter.submit_review(OWNER, REPO, PR_NUMBER, event=REVIEW_EVENT, body=REVIEW_BODY)

    msg = str(exc_info.value)
    assert "503" in msg
    assert "3 attempts" in msg
    assert TOKEN not in msg
    assert tries == 3  # exactly the budget — no more, no fewer


# ---------------------------------------------------------------------------
# submit_review — no retry on non-transient 4xx
# ---------------------------------------------------------------------------


def test_submit_review_no_retry_on_422() -> None:
    tries = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal tries
        tries += 1
        return httpx.Response(422, json={"message": "Validation Failed"})

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    with pytest.raises(GitProviderAdapterError, match="422"):
        adapter.submit_review(OWNER, REPO, PR_NUMBER, event=REVIEW_EVENT, body=REVIEW_BODY)

    assert tries == 1  # NOT retried


def test_submit_review_no_retry_on_403() -> None:
    tries = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal tries
        tries += 1
        return httpx.Response(403, json={"message": "Forbidden"})

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    with pytest.raises(GitProviderAdapterError, match="403"):
        adapter.submit_review(OWNER, REPO, PR_NUMBER, event=REVIEW_EVENT, body=REVIEW_BODY)

    assert tries == 1  # NOT retried


def test_submit_review_no_retry_on_404() -> None:
    tries = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal tries
        tries += 1
        return httpx.Response(404, json={"message": "Not Found"})

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    with pytest.raises(GitProviderAdapterError, match="404"):
        adapter.submit_review(OWNER, REPO, PR_NUMBER, event=REVIEW_EVENT, body=REVIEW_BODY)

    assert tries == 1  # NOT retried


# ---------------------------------------------------------------------------
# submit_review — token redaction
# ---------------------------------------------------------------------------


def test_submit_review_token_redacted_from_error_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        # 422 body that happens to echo the token — must be redacted.
        return httpx.Response(422, text=f"validation error: bad token {TOKEN}")

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    with pytest.raises(GitProviderAdapterError) as exc_info:
        adapter.submit_review(OWNER, REPO, PR_NUMBER, event=REVIEW_EVENT, body=REVIEW_BODY)

    msg = str(exc_info.value)
    assert TOKEN not in msg
    assert "***" in msg


# ---------------------------------------------------------------------------
# submit_review — local validation (ValueError before any HTTP)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("which", ["owner", "repo"])
def test_submit_review_empty_owner_or_repo_raises_value_error_before_any_http(
    which: str,
) -> None:
    called: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        called.append(request)
        return httpx.Response(200, json=_review_response())

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    if which == "owner":
        with pytest.raises(ValueError, match="owner"):
            adapter.submit_review("", REPO, PR_NUMBER, event=REVIEW_EVENT, body=REVIEW_BODY)
    else:
        with pytest.raises(ValueError, match="repo"):
            adapter.submit_review(OWNER, "", PR_NUMBER, event=REVIEW_EVENT, body=REVIEW_BODY)

    assert called == []  # no HTTP call made — validated locally


def test_submit_review_invalid_number_raises_value_error_before_any_http() -> None:
    called: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        called.append(request)
        return httpx.Response(200, json=_review_response())

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    with pytest.raises(ValueError, match="number"):
        adapter.submit_review(OWNER, REPO, 0, event=REVIEW_EVENT, body=REVIEW_BODY)

    assert called == []  # no HTTP call made — validated locally


# ---------------------------------------------------------------------------
# submit_review — format-agnostic pass-through (ID-066)
# ---------------------------------------------------------------------------


def test_submit_review_carries_unusual_event_verbatim() -> None:
    # ID-066: an unusual event string (e.g. "DISMISS") is carried to GitHub
    # verbatim — no validation, no coercion. The adapter does not judge it.
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(200, json=_review_response())

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    adapter.submit_review(OWNER, REPO, PR_NUMBER, event="DISMISS", body=REVIEW_BODY)

    assert captured["payload"] == {"event": "DISMISS", "body": REVIEW_BODY}


def test_submit_review_has_no_event_validation_logic() -> None:
    # ID-066 (structural): submit_review performs NO local validation of `event`
    # — no review-status enum reference, no .upper() coercion, no `if event`
    # branching. The string is passed straight into the payload dict.
    import workspace_worker.repo.git.adapter as adapter_mod

    src = inspect.getsource(adapter_mod.GitProviderAdapter.submit_review)
    assert "ReviewStatus" not in src
    assert ".upper()" not in src
    assert "if event" not in src


def test_submit_review_body_optionality_empty_body_succeeds() -> None:
    # `body` is a plain str (not Optional); an empty body is valid and carried
    # through verbatim (e.g. an APPROVE with no comment).
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(200, json=_review_response())

    adapter = GitProviderAdapter(TOKEN, client=_client(handler), max_attempts=3)
    result = adapter.submit_review(OWNER, REPO, PR_NUMBER, event=REVIEW_EVENT, body="")

    assert captured["payload"] == {"event": REVIEW_EVENT, "body": ""}
    assert result == ReviewResult(
        owner=OWNER, repo=REPO, number=PR_NUMBER, review_id=REVIEW_ID, state=REVIEW_STATE
    )
