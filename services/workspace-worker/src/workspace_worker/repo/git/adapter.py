"""Git Provider Adapter — branch + push via the GitHub REST API (SFP-58).

Creates or updates a remote GitHub ref (a branch, expressed as its partial ref
path such as ``heads/sfp-58-x``) using the GitHub ``git refs`` REST API, with
``httpx`` for HTTP and ``tenacity`` for retry of transient failures.

This is the in-adapter direct-httpx realization of the Git Provider Adapter
(ID-034 / ID-035 / ID-051). It is the *push* slice — ``RepoManager``
(``workspace_worker.repo.manager``) owns clone/local lifecycle (SFP-38).

Security model — the token never leaves memory and is never persisted
(mirroring ``RepoManager``, ID-035):

* The token is supplied by the caller, already resolved from configuration
  (ID-016 / SFP-28); this module never reads secrets directly.
* The token is carried only as a per-request ``Authorization: Bearer <token>``
  header (ID-034) — there are no token-bearing URLs and no on-disk artifacts.
* The token is redacted from every error surfaced by this module
  (see :func:`_redact`); the module never logs the client or its headers.

Push semantics: a GET probe against
``/repos/{owner}/{repo}/git/refs/{ref}`` decides create-vs-update — a ``404``
means the ref does not yet exist (``POST`` to create it, ``created=True``);
a ``200`` means it exists (``PATCH`` to fast-forward it, ``created=False``).
Transient failures — HTTP ``429``/``5xx`` and the network errors
``ConnectError``/``ReadTimeout``/``RemoteProtocolError`` — are retried with
exponential backoff + jitter; other ``4xx`` are surfaced immediately as a
redacted :class:`GitProviderAdapterError`.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from tenacity import (
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

__all__ = [
    "GitDeleteResult",
    "GitProviderAdapter",
    "GitProviderAdapterError",
    "GitPushResult",
    "GitSyncResult",
    "PullRequestResult",
    "ReviewResult",
]

#: Placeholder substituted for the token anywhere it would appear in errors.
_REDACTED = "***"

#: GitHub REST API root (overridable in the constructor for tests/GHES).
_DEFAULT_BASE_URL = "https://api.github.com"

#: Default retry budget — max number of attempts per HTTP request. Overridable
#: via the ``max_attempts`` constructor argument so retry-exhaust tests are fast
#: (e.g. ``max_attempts=3``).
_DEFAULT_MAX_ATTEMPTS = 5

#: HTTP statuses that are always transient and therefore retried. Every other
#: 4xx (e.g. 401/403/404/422) surfaces immediately as a redacted error.
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


class GitProviderAdapterError(RuntimeError):
    """Raised when a Git Provider operation fails.

    The token is guaranteed absent from the message (see :func:`_redact`).
    """


@dataclass(frozen=True, slots=True)
class GitPushResult:
    """Outcome of :meth:`GitProviderAdapter.push_branch`.

    Attributes:
        owner: Repository owner (account or organization).
        repo: Repository name.
        ref: The partial ref path pushed (e.g. ``heads/sfp-58-x``).
        sha: The commit SHA the ref was moved to.
        created: ``True`` if the ref was newly created (``POST``); ``False`` if
            an existing ref was fast-forwarded (``PATCH``).
    """

    owner: str
    repo: str
    ref: str
    sha: str
    created: bool


@dataclass(frozen=True, slots=True)
class GitDeleteResult:
    """Outcome of :meth:`GitProviderAdapter.delete_ref`.

    Attributes:
        owner: Repository owner (account or organization).
        repo: Repository name.
        ref: The partial ref path deleted (e.g. ``heads/sfp-57-x``).
    """

    owner: str
    repo: str
    ref: str


@dataclass(frozen=True, slots=True)
class PullRequestResult:
    """Outcome of :meth:`GitProviderAdapter.create_pr` / :meth:`update_pr`.

    Attributes:
        owner: Repository owner (account or organization).
        repo: Repository name.
        number: The pull request number (GitHub integer id).
        url: The pull request's human-readable URL, sourced from the GitHub
            response ``html_url`` field.
        state: The pull request state string (e.g. ``open`` / ``closed``).
    """

    owner: str
    repo: str
    number: int
    url: str
    state: str


@dataclass(frozen=True, slots=True)
class GitSyncResult:
    """Outcome of :meth:`GitProviderAdapter.sync_branch`.

    Attributes:
        owner: Repository owner (account or organization).
        repo: Repository name.
        pull_number: The pull request number whose head branch was synced.
        sha: The commit SHA the head branch was moved to, parsed from the last
            path segment of the GitHub response ``url`` field. Empty string
            when the field is absent or unparseable (graceful degradation).
    """

    owner: str
    repo: str
    pull_number: int
    sha: str


@dataclass(frozen=True, slots=True)
class ReviewResult:
    """Outcome of :meth:`GitProviderAdapter.submit_review`.

    Attributes:
        owner: Repository owner (account or organization).
        repo: Repository name.
        number: The pull request number the review was submitted on.
        review_id: The review id, parsed from the GitHub response ``id`` field.
        state: The review state string (e.g. ``APPROVED`` /
            ``CHANGES_REQUESTED`` / ``COMMENTED`` / ``PENDING``), parsed from the
            response ``state`` field.
    """

    owner: str
    repo: str
    number: int
    review_id: int
    state: str


class _TransientHTTPError(Exception):
    """Internal signal: a retryable HTTP status was observed.

    Raised inside the request loop to drive ``tenacity`` retry; it never escapes
    this module (the give-up path converts it to a redacted
    :class:`GitProviderAdapterError`).
    """

    def __init__(self, response: httpx.Response) -> None:
        super().__init__(f"transient HTTP {response.status_code}")
        self.response = response


def _redact(text: str, token: str) -> str:
    """Replace every occurrence of ``token`` in ``text`` with ``***``.

    A token that is empty/falsy disables redaction (nothing to leak) — mirrors
    ``RepoManager._redact`` (ID-035).
    """
    return text.replace(token, _REDACTED) if token else text


def _last_url_path_segment(url: str) -> str:
    """Return the last non-empty path segment of ``url``.

    GitHub's ``update-branch`` 202 response carries a ``url`` (a git-ref or
    commit URL) whose last path segment is the resulting sha/branch we surface.
    This is a deliberately simple, defensive heuristic — any parse failure
    degrades to an empty string and never raises.
    """
    try:
        path = httpx.URL(url).path
    except Exception:
        return ""
    segments = [s for s in path.split("/") if s]
    return segments[-1] if segments else ""


class GitProviderAdapter:
    """Creates/updates a remote GitHub ref (branch) via the REST API.

    The token is held in memory only and carried as a per-request
    ``Authorization: Bearer <token>`` header (ID-034); it is never persisted
    and never logged. A fresh ref is created (``POST``) when the probe 404s, or
    an existing ref is fast-forwarded (``PATCH``) when the probe 200s.

    Args:
        token: GitHub access token (PAT), caller-resolved from sfp-config
            (ID-016 / SFP-28). An empty token is accepted — redaction then
            becomes a no-op, mirroring ``RepoManager``.
        client: Injectable :class:`httpx.Client` (tests inject a client built on
            :class:`httpx.MockTransport`). When ``None``, a default client
            carrying the bearer auth header is constructed. The bearer header is
            added to *every* request regardless, so an injected client need not
            carry it.
        base_url: GitHub API base URL (defaults to ``https://api.github.com``;
            override for GitHub Enterprise).
        max_attempts: Retry budget — maximum number of attempts per HTTP request
            on transient failures. Override with a small value (e.g. ``3``) so
            retry-exhaust tests are fast.
    """

    def __init__(
        self,
        token: str,
        *,
        client: httpx.Client | None = None,
        base_url: str = _DEFAULT_BASE_URL,
        max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
    ) -> None:
        self._token = token
        self._base = base_url.rstrip("/")
        self._max_attempts = max_attempts
        self._client = client if client is not None else self._default_client(token)

    @staticmethod
    def _default_client(token: str) -> httpx.Client:
        # The bearer header is also set per-request in _request, so this default
        # is belt-and-braces — it guarantees auth even if a caller forgets to
        # inject a client (and never logs the header).
        return httpx.Client(headers={"Authorization": f"Bearer {token}"})

    def _retryer(self) -> Retrying:
        return Retrying(
            stop=stop_after_attempt(self._max_attempts),
            wait=wait_exponential_jitter(initial=0.25, max=10.0),
            retry=retry_if_exception_type(
                (
                    httpx.ConnectError,
                    httpx.ReadTimeout,
                    httpx.RemoteProtocolError,
                    _TransientHTTPError,
                )
            ),
            reraise=True,
        )

    def _request(
        self,
        method: str,
        url: str,
        *,
        json: dict[str, str] | None = None,
    ) -> httpx.Response:
        """Issue one HTTP request, retrying transient failures via tenacity.

        Retryable HTTP statuses are converted to :class:`_TransientHTTPError`
        inside the loop so ``tenacity`` can drive the retry; network errors are
        retried directly. Any exhaustion is converted to a redacted
        :class:`GitProviderAdapterError`.
        """
        headers = {"Authorization": f"Bearer {self._token}"}

        def _do() -> httpx.Response:
            response = self._client.request(method, url, json=json, headers=headers)
            if response.status_code in _RETRY_STATUSES:
                raise _TransientHTTPError(response)
            return response

        try:
            return self._retryer()(_do)
        except _TransientHTTPError as exc:
            raise GitProviderAdapterError(
                _redact(
                    f"{method} {url} failed: HTTP {exc.response.status_code} "
                    f"{exc.response.reason_phrase} after {self._max_attempts} attempts",
                    self._token,
                )
            ) from exc
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError) as exc:
            raise GitProviderAdapterError(
                _redact(
                    f"{method} {url} failed after {self._max_attempts} attempts: "
                    f"{type(exc).__name__}: {exc}",
                    self._token,
                )
            ) from exc

    def _raise_for_status(self, action: str, response: httpx.Response, url: str) -> None:
        """Surface a non-success response as a redacted :class:`GitProviderAdapterError`.

        Retryable statuses never reach here (they are retried in :meth:`_request`);
        a success response is a no-op. The response body is included for
        debuggability, redacted of the token.
        """
        if response.is_success:
            return
        raise GitProviderAdapterError(
            _redact(
                f"GitHub {action} failed: HTTP {response.status_code} "
                f"{response.reason_phrase} for {url}: {response.text}",
                self._token,
            )
        )

    def push_branch(self, owner: str, repo: str, ref: str, sha: str) -> GitPushResult:
        """Create or update the remote ref ``ref`` to point at ``sha``.

        Probes ``GET /repos/{owner}/{repo}/git/refs/{ref}`` first:

        * ``404`` → the ref is absent → ``POST /repos/{owner}/{repo}/git/refs``
          with ``{"ref": ref, "sha": sha}`` to create it (``created=True``).
        * ``200`` → the ref exists → ``PATCH /repos/{owner}/{repo}/git/refs/{ref}``
          with ``{"sha": sha}`` to fast-forward it (``created=False``).

        Any other non-success probe response surfaces immediately as a redacted
        :class:`GitProviderAdapterError`.

        Args:
            owner: Repository owner (account or organization).
            repo: Repository name.
            ref: Partial ref path to push (e.g. ``heads/sfp-58-x``). Must be
                non-empty; an empty ref raises :class:`ValueError` before any
                HTTP call.
            sha: The commit SHA the ref should point at.

        Returns:
            The :class:`GitPushResult` describing the outcome.

        Raises:
            ValueError: if ``ref`` is empty (before any network call).
            GitProviderAdapterError: if any request ultimately fails after
                retries, or a non-retryable error is returned. The token is
                redacted from the message.
        """
        if not ref:
            raise ValueError("ref must not be empty")
        refs_base = f"{self._base}/repos/{owner}/{repo}/git/refs"
        probe_url = f"{refs_base}/{ref}"
        probe = self._request("GET", probe_url)
        if probe.status_code == httpx.codes.NOT_FOUND:
            create = self._request("POST", refs_base, json={"ref": ref, "sha": sha})
            self._raise_for_status("create ref", create, refs_base)
            return GitPushResult(owner=owner, repo=repo, ref=ref, sha=sha, created=True)
        # The probe is not a 404 — if it is also not a success, surface it; a
        # 2xx means the ref exists and we fast-forward via PATCH.
        self._raise_for_status("probe ref", probe, probe_url)
        update = self._request("PATCH", probe_url, json={"sha": sha})
        self._raise_for_status("update ref", update, probe_url)
        return GitPushResult(owner=owner, repo=repo, ref=ref, sha=sha, created=False)

    def delete_ref(self, owner: str, repo: str, ref: str) -> GitDeleteResult:
        """Delete the remote ref ``ref`` (a branch, expressed as its partial ref path).

        Issues ``DELETE {base_url}/repos/{owner}/{repo}/git/refs/{ref}`` with no
        request body, reusing :meth:`_request` (bearer auth + tenacity retry on
        ``{429,500,502,503,504}`` and the network errors, no retry on other
        ``4xx``) and :meth:`_raise_for_status` (a redacted
        :class:`GitProviderAdapterError` on any non-success).

        The caller maps a branch name to the partial-ref form before delegating
        (``<branch>`` -> ``heads/<branch>``). A ``404`` (ref already gone) is
        surfaced as a redacted :class:`GitProviderAdapterError` and is **not**
        swallowed — end-to-end remote-delete idempotency on an already-gone ref
        is an explicit out-of-scope refinement for a future ticket.

        Args:
            owner: Repository owner (account or organization).
            repo: Repository name.
            ref: Partial ref path to delete (e.g. ``heads/sfp-57-x``). Must be
                non-empty; an empty ref raises :class:`ValueError` before any
                HTTP call.

        Returns:
            The :class:`GitDeleteResult` describing the outcome.

        Raises:
            ValueError: if ``ref`` is empty (before any network call).
            GitProviderAdapterError: if the request ultimately fails after
                retries, or a non-retryable error (incl. ``404``) is returned.
                The token is redacted from the message.
        """
        if not ref:
            raise ValueError("ref must not be empty")
        url = f"{self._base}/repos/{owner}/{repo}/git/refs/{ref}"
        response = self._request("DELETE", url)
        self._raise_for_status("delete ref", response, url)
        return GitDeleteResult(owner=owner, repo=repo, ref=ref)

    def create_pr(
        self,
        owner: str,
        repo: str,
        *,
        title: str,
        head: str,
        base: str,
        body: str,
    ) -> PullRequestResult:
        """Create a pull request via ``POST /repos/{owner}/{repo}/pulls``.

        Issues the GitHub pull-request create endpoint with a JSON payload of
        ``{title, head, base, body}`` via :meth:`_request` (bearer auth +
        tenacity retry on ``{429,500,502,503,504}`` and the network errors, no
        retry on other ``4xx``) and :meth:`_raise_for_status` (a redacted
        :class:`GitProviderAdapterError` on any non-success).

        This adapter is **format-agnostic** — it carries no title/body
        formatting knowledge and passes ``title`` / ``body`` through verbatim;
        the caller formats them.

        Args:
            owner: Repository owner (account or organization).
            repo: Repository name.
            title: Pull request title (caller-formatted). Must be non-empty.
            head: The head branch name the PR's changes come from.
            base: The base branch name the PR targets.
            body: Pull request body (caller-formatted).

        Returns:
            The :class:`PullRequestResult` parsed from the response JSON
            (``number`` / ``html_url`` / ``state``).

        Raises:
            ValueError: if ``owner`` / ``repo`` / ``title`` / ``head`` / ``base``
                is empty (before any network call).
            GitProviderAdapterError: if the request ultimately fails after
                retries, or a non-retryable error is returned. The token is
                redacted from the message.
        """
        if not owner:
            raise ValueError("owner must not be empty")
        if not repo:
            raise ValueError("repo must not be empty")
        if not title:
            raise ValueError("title must not be empty")
        if not head:
            raise ValueError("head must not be empty")
        if not base:
            raise ValueError("base must not be empty")
        url = f"{self._base}/repos/{owner}/{repo}/pulls"
        payload = {"title": title, "head": head, "base": base, "body": body}
        response = self._request("POST", url, json=payload)
        self._raise_for_status("create pull request", response, url)
        data = response.json()
        return PullRequestResult(
            owner=owner,
            repo=repo,
            number=data["number"],
            url=data["html_url"],
            state=data["state"],
        )

    def update_pr(
        self,
        owner: str,
        repo: str,
        number: int,
        *,
        title: str | None = None,
        body: str | None = None,
        state: str | None = None,
    ) -> PullRequestResult:
        """Update a pull request via ``PATCH /repos/{owner}/{repo}/pulls/{number}``.

        Issues the GitHub pull-request update endpoint with a JSON body
        containing **only** the non-``None`` fields among ``{title, body,
        state}`` (``None`` fields are omitted, not serialized as ``null``) via
        :meth:`_request` (bearer auth + tenacity retry on
        ``{429,500,502,503,504}`` and the network errors, no retry on other
        ``4xx``) and :meth:`_raise_for_status` (a redacted
        :class:`GitProviderAdapterError` on any non-success).

        This adapter is **format-agnostic** — it carries no title/body
        formatting knowledge and passes ``title`` / ``body`` through verbatim;
        ``state`` accepts the GitHub values (``open`` / ``closed``) and is
        passed through without validation (invalid values surface as a redacted
        ``422`` error).

        Args:
            owner: Repository owner (account or organization).
            repo: Repository name.
            number: The pull request number. Must be ``>= 1``.
            title: New title (caller-formatted); ``None`` to leave unchanged.
            body: New body (caller-formatted); ``None`` to leave unchanged.
            state: New state (e.g. ``open`` / ``closed``); ``None`` to leave
                unchanged.

        Returns:
            The :class:`PullRequestResult` parsed from the response JSON
            (``number`` / ``html_url`` / ``state``).

        Raises:
            ValueError: if ``owner`` / ``repo`` is empty, if ``number < 1``, or
                if all of ``title`` / ``body`` / ``state`` are ``None`` (empty
                payload) — all raised before any network call.
            GitProviderAdapterError: if the request ultimately fails after
                retries, or a non-retryable error is returned. The token is
                redacted from the message.
        """
        if not owner:
            raise ValueError("owner must not be empty")
        if not repo:
            raise ValueError("repo must not be empty")
        if number < 1:
            raise ValueError("number must be >= 1")
        payload: dict[str, str] = {}
        if title is not None:
            payload["title"] = title
        if body is not None:
            payload["body"] = body
        if state is not None:
            payload["state"] = state
        if not payload:
            raise ValueError("update_pr requires at least one of title/body/state")
        url = f"{self._base}/repos/{owner}/{repo}/pulls/{number}"
        response = self._request("PATCH", url, json=payload)
        self._raise_for_status("update pull request", response, url)
        data = response.json()
        return PullRequestResult(
            owner=owner,
            repo=repo,
            number=data["number"],
            url=data["html_url"],
            state=data["state"],
        )

    def submit_review(
        self,
        owner: str,
        repo: str,
        number: int,
        *,
        event: str,
        body: str,
    ) -> ReviewResult:
        """Submit a pull request review via ``POST /repos/{...}/pulls/{number}/reviews``.

        Issues the GitHub submit-review endpoint with a JSON payload of
        ``{event, body}`` via :meth:`_request` (bearer auth + tenacity retry on
        ``{429,500,502,503,504}`` and the network errors, no retry on other
        ``4xx``) and :meth:`_raise_for_status` (a redacted
        :class:`GitProviderAdapterError` on any non-success).

        This adapter is **format-agnostic / pass-through** on ``event``
        (ID-066): the ``event`` string is carried to GitHub verbatim with **no
        local validation** and this module imports no review-status enum. Mapping
        a review-status value to the GitHub ``event`` string (``APPROVE`` /
        ``REQUEST_CHANGES`` / ``COMMENT``) is the **caller's** job; an invalid
        ``event`` surfaces as a redacted GitHub ``422``.

        Args:
            owner: Repository owner (account or organization).
            repo: Repository name.
            number: The pull request number. Must be ``>= 1``.
            event: The review event string (caller-mapped from a review-status
                value, e.g. ``APPROVE``). Carried through verbatim — not validated.
            body: The review body (caller-formatted). May be empty.

        Returns:
            The :class:`ReviewResult` parsed from the response JSON (``id`` ->
            ``review_id``, ``state`` -> ``state``).

        Raises:
            ValueError: if ``owner`` / ``repo`` is empty or ``number < 1``
                (before any network call).
            GitProviderAdapterError: if the request ultimately fails after
                retries, or a non-retryable error (e.g. ``422`` for an invalid
                ``event``) is returned. The token is redacted from the message.
        """
        if not owner:
            raise ValueError("owner must not be empty")
        if not repo:
            raise ValueError("repo must not be empty")
        if number < 1:
            raise ValueError("number must be >= 1")
        url = f"{self._base}/repos/{owner}/{repo}/pulls/{number}/reviews"
        response = self._request("POST", url, json={"event": event, "body": body})
        self._raise_for_status("submit review", response, url)
        data = response.json()
        return ReviewResult(
            owner=owner,
            repo=repo,
            number=number,
            review_id=data["id"],
            state=data["state"],
        )

    def sync_branch(self, owner: str, repo: str, pull_number: int) -> GitSyncResult:
        """Sync (update) a pull request's head branch with its base via ``update-branch``.

        Issues ``PUT {base}/repos/{owner}/{repo}/pulls/{pull_number}/update-branch``
        with an empty body via :meth:`_request` (bearer auth + tenacity retry on
        ``{429,500,502,503,504}`` and the network errors, no retry on other
        ``4xx``) and :meth:`_raise_for_status` (a redacted
        :class:`GitProviderAdapterError` on any non-success).

        GitHub returns ``202 Accepted`` on success — the update is queued
        asynchronously. Any ``2xx`` is treated as success and this method does
        **not** poll for completion (poll-until-merged is out of scope). The
        resulting head SHA is parsed from the response ``url`` field's last path
        segment and degrades to an empty string when the field is absent or
        unparseable.

        A ``409 Conflict`` (merge conflict) is a **normal failure**, not a
        blocked state (ID-068): it is not retried (``409`` is not in
        :data:`_RETRY_STATUSES`) and surfaces as a plain redacted
        :class:`GitProviderAdapterError`. There is no separate "blocked"
        exception type — callers distinguishing a conflict downstream must
        inspect the redacted message text.

        Args:
            owner: Repository owner (account or organization).
            repo: Repository name.
            pull_number: The pull request number whose head branch to sync.
                Must be ``>= 1``.

        Returns:
            The :class:`GitSyncResult` describing the outcome.

        Raises:
            ValueError: if ``owner`` / ``repo`` is empty or ``pull_number < 1``
                (before any network call).
            GitProviderAdapterError: if the request ultimately fails after
                retries, or a non-retryable error (incl. ``409`` conflict) is
                returned. The token is redacted from the message.
        """
        if not owner:
            raise ValueError("owner must not be empty")
        if not repo:
            raise ValueError("repo must not be empty")
        if pull_number < 1:
            raise ValueError("pull_number must be >= 1")
        url = f"{self._base}/repos/{owner}/{repo}/pulls/{pull_number}/update-branch"
        response = self._request("PUT", url)
        self._raise_for_status("sync branch", response, url)
        data = response.json()
        response_url = data.get("url") if isinstance(data, dict) else None
        sha = _last_url_path_segment(response_url) if response_url else ""
        return GitSyncResult(owner=owner, repo=repo, pull_number=pull_number, sha=sha)
