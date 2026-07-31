"""Jira Cloud REST client — fetch issues + transition status (SFP-225).

The Jira slice of the Workspace Worker's repo adapters: an in-adapter direct-
httpx realization (mirroring :mod:`workspace_worker.repo.git.adapter`) that
fetches a Jira issue (parsing its ADF description into a
:class:`~sfp_contracts.agents.readiness.ParsedTicket`) and transitions its
status via the Jira Cloud REST API v3. ``httpx`` carries the HTTP;
``tenacity`` drives retry of transient failures.

Security model — the API token never leaves memory and is never persisted
(mirroring :mod:`workspace_worker.repo.git.adapter`, ID-035):

* The token is supplied by the caller, already resolved from configuration
  (ID-016); this module never reads secrets directly.
* Auth is **HTTP Basic** — ``Authorization: Basic base64(email:token)`` — the
  credential model the Jira Cloud REST API requires (precedent:
  :func:`tools.jira_status._request`, line 67). It is carried only as a
  per-request header plus a default-client belt-and-braces; there are no
  token-bearing URLs and no on-disk artifacts. (Not ``Bearer`` — Jira Cloud's
  REST API does not accept a bearer PAT for these endpoints.)
* The token is redacted from every error surfaced by this module
  (see :func:`_redact`); the module never logs the client or its headers.

Transient failures — HTTP ``429``/``5xx`` and the network errors
``ConnectError``/``ReadTimeout``/``RemoteProtocolError`` — are retried with
exponential backoff + jitter; other ``4xx`` (e.g. ``404``) surface immediately
as a redacted :class:`JiraClientError`.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass

import httpx
from sfp_contracts.agents.readiness import ParsedTicket
from tenacity import (
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from workspace_worker.repo.jira.parser import adf_to_parsed_ticket

__all__ = [
    "JiraClient",
    "JiraClientError",
    "JiraIssueResult",
    "JiraTransitionResult",
]

#: Placeholder substituted for the token anywhere it would appear in errors.
_REDACTED = "***"

#: Default retry budget — max number of attempts per HTTP request. Overridable
#: via the ``max_attempts`` constructor argument so retry-exhaust tests are fast
#: (e.g. ``max_attempts=2``).
_DEFAULT_MAX_ATTEMPTS = 5

#: HTTP statuses that are always transient and therefore retried. Every other
#: 4xx (e.g. 401/403/404) surfaces immediately as a redacted error.
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


class JiraClientError(RuntimeError):
    """Raised when a Jira operation fails.

    The token is guaranteed absent from the message (see :func:`_redact`).
    """


@dataclass(frozen=True, slots=True)
class JiraIssueResult:
    """Outcome of :meth:`JiraClient.fetch_issue`.

    Attributes:
        key: The issue key (e.g. ``SFP-225``).
        summary: The issue ``summary`` field.
        status: The issue's status name (``fields.status.name``).
        labels: The issue's labels (``fields.labels``) as a tuple.
        parsed: The ADF ``description`` parsed into a :class:`ParsedTicket`
            (all fields ``None`` when the description is absent).
        raw_description: The raw ``description`` value from Jira (the ADF dict
            or ``None``), retained for callers that need the un-parsed shape.
    """

    key: str
    summary: str
    status: str
    labels: tuple[str, ...]
    parsed: ParsedTicket
    raw_description: object


@dataclass(frozen=True, slots=True)
class JiraTransitionResult:
    """Outcome of :meth:`JiraClient.transition`.

    Attributes:
        key: The issue key that was transitioned.
        transition_id: The transition id that was applied (e.g. ``31`` / ``41``
            / ``51`` per :data:`tools.jira_status.TRANSITIONS`).
    """

    key: str
    transition_id: str


class _TransientHTTPError(Exception):
    """Internal signal: a retryable HTTP status was observed.

    Raised inside the request loop to drive ``tenacity`` retry; it never escapes
    this module (the give-up path converts it to a redacted
    :class:`JiraClientError`).
    """

    def __init__(self, response: httpx.Response) -> None:
        super().__init__(f"transient HTTP {response.status_code}")
        self.response = response


def _redact(text: str, token: str) -> str:
    """Replace every occurrence of ``token`` in ``text`` with ``***``.

    A token that is empty/falsy disables redaction (nothing to leak) — mirrors
    ``GitProviderAdapter._redact`` (ID-035).
    """
    return text.replace(token, _REDACTED) if token else text


class JiraClient:
    """Fetch Jira issues + transition status via the Jira Cloud REST API v3.

    The API token is held in memory only and carried as a per-request
    ``Authorization: Basic base64(email:token)`` header (Jira Cloud's credential
    model — not Bearer); it is never persisted and never logged.

    Args:
        site: Jira Cloud base URL (e.g. ``https://arconta.atlassian.net``).
        email: The Atlassian account email (the Basic-auth user).
        token: Jira API token (caller-resolved from sfp-config, ID-016). An
            empty token is accepted — redaction then becomes a no-op, mirroring
            ``GitProviderAdapter``.
        client: Injectable :class:`httpx.Client` (tests inject a client built on
            :class:`httpx.MockTransport`). When ``None``, a default client
            carrying the Basic header is constructed. The Basic header is added
            to *every* request regardless, so an injected client need not carry
            it.
        max_attempts: Retry budget — maximum number of attempts per HTTP request
            on transient failures. Override with a small value (e.g. ``2``) so
            retry-exhaust tests are fast.
    """

    def __init__(
        self,
        site: str,
        email: str,
        token: str,
        *,
        client: httpx.Client | None = None,
        max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
    ) -> None:
        self._site = site.rstrip("/")
        self._email = email
        self._token = token
        self._max_attempts = max_attempts
        self._client = client if client is not None else self._default_client()

    def _basic_auth_header(self) -> str:
        """Return the ``Authorization: Basic ...`` header value for this client."""
        raw = f"{self._email}:{self._token}".encode()
        return "Basic " + base64.b64encode(raw).decode()

    def _default_client(self) -> httpx.Client:
        # The Basic header is also set per-request in _request, so this default
        # is belt-and-braces — it guarantees auth even if a caller forgets to
        # inject a client (and never logs the header).
        return httpx.Client(headers={"Authorization": self._basic_auth_header()})

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
        json: dict[str, object] | None = None,
    ) -> httpx.Response:
        """Issue one HTTP request, retrying transient failures via tenacity.

        Retryable HTTP statuses are converted to :class:`_TransientHTTPError`
        inside the loop so ``tenacity`` can drive the retry; network errors are
        retried directly. Any exhaustion is converted to a redacted
        :class:`JiraClientError`.
        """
        headers = {"Authorization": self._basic_auth_header()}

        def _do() -> httpx.Response:
            response = self._client.request(method, url, json=json, headers=headers)
            if response.status_code in _RETRY_STATUSES:
                raise _TransientHTTPError(response)
            return response

        try:
            return self._retryer()(_do)
        except _TransientHTTPError as exc:
            raise JiraClientError(
                _redact(
                    f"{method} {url} failed: HTTP {exc.response.status_code} "
                    f"{exc.response.reason_phrase} after {self._max_attempts} attempts",
                    self._token,
                )
            ) from exc
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError) as exc:
            raise JiraClientError(
                _redact(
                    f"{method} {url} failed after {self._max_attempts} attempts: "
                    f"{type(exc).__name__}: {exc}",
                    self._token,
                )
            ) from exc

    def _raise_for_status(self, action: str, response: httpx.Response, url: str) -> None:
        """Surface a non-success response as a redacted :class:`JiraClientError`.

        Retryable statuses never reach here (they are retried in :meth:`_request`);
        a success response is a no-op. The response body is included for
        debuggability, redacted of the token.
        """
        if response.is_success:
            return
        raise JiraClientError(
            _redact(
                f"Jira {action} failed: HTTP {response.status_code} "
                f"{response.reason_phrase} for {url}: {response.text}",
                self._token,
            )
        )

    def fetch_issue(self, key: str) -> JiraIssueResult:
        """Fetch a Jira issue and parse its ADF description into a :class:`ParsedTicket`.

        Issues ``GET {site}/rest/api/3/issue/{key}?fields=summary,description,
        status,labels`` via :meth:`_request` (Basic auth + tenacity retry on
        ``{429,500,502,503,504}`` and the network errors, no retry on other
        ``4xx``) and :meth:`_raise_for_status` (a redacted
        :class:`JiraClientError` on any non-success). The ``description`` field
        (ADF JSON) is parsed with :func:`adf_to_parsed_ticket`; a ``None``
        description yields an all-None :class:`ParsedTicket`.

        Args:
            key: The issue key (e.g. ``SFP-225``). Must be non-empty; an empty
                key raises :class:`ValueError` before any HTTP call.

        Returns:
            The :class:`JiraIssueResult` carrying key/summary/status/labels, the
            parsed :class:`ParsedTicket`, and the raw description.

        Raises:
            ValueError: if ``key`` is empty (before any network call).
            JiraClientError: if the request ultimately fails after retries, or a
                non-retryable error (e.g. ``404``) is returned. The token is
                redacted from the message.
        """
        if not key:
            raise ValueError("key must not be empty")
        url = f"{self._site}/rest/api/3/issue/{key}?fields=summary,description,status,labels"
        response = self._request("GET", url)
        self._raise_for_status("fetch issue", response, url)
        data = response.json()
        fields = data.get("fields", {}) if isinstance(data, dict) else {}
        description = fields.get("description") if isinstance(fields, dict) else None
        if isinstance(description, dict):
            parsed = adf_to_parsed_ticket(description)
        else:
            parsed = ParsedTicket()
        status_obj = fields.get("status") if isinstance(fields, dict) else None
        status = ""
        if isinstance(status_obj, dict):
            name = status_obj.get("name", "")
            status = name if isinstance(name, str) else ""
        labels_raw = fields.get("labels") if isinstance(fields, dict) else None
        labels = tuple(labels_raw) if isinstance(labels_raw, list) else ()
        summary_raw = fields.get("summary", "") if isinstance(fields, dict) else ""
        summary = summary_raw if isinstance(summary_raw, str) else ""
        return JiraIssueResult(
            key=key,
            summary=summary,
            status=status,
            labels=labels,
            parsed=parsed,
            raw_description=description,
        )

    def transition(self, key: str, transition_id: str) -> JiraTransitionResult:
        """Transition a Jira issue's status via ``POST /rest/api/3/issue/{key}/transitions``.

        Issues the Jira transition endpoint with a JSON payload of
        ``{"transition": {"id": transition_id}}`` via :meth:`_request` (Basic
        auth + tenacity retry on ``{429,500,502,503,504}`` and the network
        errors, no retry on other ``4xx``) and :meth:`_raise_for_status` (a
        redacted :class:`JiraClientError` on any non-success). A ``204`` response
        is the Jira success signal.

        This method is **transition-id pass-through**: it carries no transition-
        name -> id table and does no name validation. Picking the right id (e.g.
        ``31`` / ``41`` / ``51`` per :data:`tools.jira_status.TRANSITIONS`) is
        the caller's job (PRSpec risk 3 — out of scope here).

        Args:
            key: The issue key (e.g. ``SFP-225``). Must be non-empty.
            transition_id: The transition id string (e.g. ``41``). Must be
                non-empty; an empty id raises :class:`ValueError` before any HTTP
                call.

        Returns:
            The :class:`JiraTransitionResult` echoing the key + transition_id.

        Raises:
            ValueError: if ``key`` or ``transition_id`` is empty (before any
                network call).
            JiraClientError: if the request ultimately fails after retries, or a
                non-retryable error is returned. The token is redacted from the
                message.
        """
        if not key:
            raise ValueError("key must not be empty")
        if not transition_id:
            raise ValueError("transition_id must not be empty")
        url = f"{self._site}/rest/api/3/issue/{key}/transitions"
        payload: dict[str, object] = {"transition": {"id": transition_id}}
        response = self._request("POST", url, json=payload)
        self._raise_for_status("transition", response, url)
        return JiraTransitionResult(key=key, transition_id=transition_id)
