"""Typed payloads for the 8 commands (MAS §5.3 / ID-031 / SFP-38).

Each command carries one of these as its ``payload``. The field lists are the
minimal natural keys/state of each command — enough to route and act on the
command without front-loading the richer per-domain schemas those consumers will
own later. Every payload rejects unknown fields (``extra='forbid'``) so schema
drift between issuer and handler surfaces immediately.

Grounded in MAS §5.3 (envelope + payload shape) and ID-031 (the command names,
excluding the internal ``GeneratePRSpecifications`` operation).
"""

from typing import ClassVar

from pydantic import BaseModel, ConfigDict


class _Payload(BaseModel):
    """Base for payloads: ``extra='forbid'`` is the only shared rule.

    Subclasses are flat (no envelope fields) — the envelope lives on
    :class:`~sfp_contracts.commands.envelope.CommandEnvelope`.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")


class ExecuteCodingJobPayload(_Payload):
    """Command a Coder to execute a coding job against a PR-spec."""

    job_id: str
    pr_spec_id: str


class SynchronizePullRequestPayload(_Payload):
    """Command a Coder to push/synchronize a pull request's branch."""

    pr_number: int
    repo: str


class CancelCodingJobPayload(_Payload):
    """Command a Coder to cancel an in-flight coding job."""

    job_id: str
    reason: str


class ReviewPullRequestPayload(_Payload):
    """Command the Reviewer to review a pull request."""

    pr_number: int
    repo: str


class CancelReviewJobPayload(_Payload):
    """Command the Reviewer to cancel an in-flight review job."""

    job_id: str
    reason: str


class RequestUserInputPayload(_Payload):
    """Command the UI to solicit free-form input from a human."""

    session_id: str
    prompt: str


class NotifyUserPayload(_Payload):
    """Command the UI to deliver a notification to a human."""

    session_id: str
    message: str


class RequestMergePayload(_Payload):
    """Command a merge of a pull request (issuer: Orchestrator, ID-072)."""

    pr_number: int
    repo: str
