"""Tests for :mod:`workspace_worker.repo.git.adapter` — the Git Provider push slice (SFP-58).

All tests inject an :class:`httpx.Client` built on :class:`httpx.MockTransport`,
so nothing ever touches ``api.github.com``. Each handler asserts the request
shape (method, URL path, bearer header, JSON payload) and returns canned
responses, including the retry-then-succeed and retry-exhaust paths.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest
from workspace_worker.repo.git.adapter import (
    GitDeleteResult,
    GitProviderAdapter,
    GitProviderAdapterError,
    GitPushResult,
    _redact,
)

TOKEN = "ghp_secrettoken_value_123"
OWNER = "arconta"
REPO = "sfp"
REF = "heads/sfp-58-git-adapter-push"
SHA = "0123456789abcdef0123456789abcdef01234567"


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
