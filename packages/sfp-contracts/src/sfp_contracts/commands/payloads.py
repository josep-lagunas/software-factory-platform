"""Typed payloads for the 8 commands (MAS §5.3 / MAS §4.7 / ID-031 / SFP-219).

Each command carries one of these as its ``payload`` (MAS §4.7: the payload half
of the message envelope). The field lists are the minimal natural keys/state of
each command — enough to route and act on the command without front-loading the
richer per-domain schemas those consumers will own later. Every payload rejects
unknown fields (``extra='forbid'``) so schema drift between issuer and handler
surfaces immediately.

The concrete command names live HERE now (SFP-219): the former per-message
envelope subclasses are gone, and each command is modelled by its payload class
(dropping the ``…Payload`` suffix, imperative grammar). Every payload subclasses
the public :class:`CommandPayload` base. The command discriminator
(``command_type``) and the envelope live on
:class:`~sfp_contracts.commands.envelope.CommandEnvelope`.

Grounded in MAS §5.3 (command catalogue), MAS §4.7 (envelope + payload shape)
and ID-031 (the command names, excluding the internal
``GeneratePRSpecifications`` operation).
"""

from typing import ClassVar

from pydantic import BaseModel, ConfigDict


class CommandPayload(BaseModel):
    """Base for command payloads: ``extra='forbid'`` is the only shared rule.

    Subclasses are flat (no envelope fields) — the envelope lives on
    :class:`~sfp_contracts.commands.envelope.CommandEnvelope`. The concrete
    command names are the payload classes themselves (MAS §4.7 / SFP-219).
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")


class ExecuteCodingJob(CommandPayload):
    """Command a Coder to execute a coding job against a PR-spec."""

    job_id: str
    pr_spec_id: str


class SynchronizePullRequest(CommandPayload):
    """Command a Coder to push/synchronize a pull request's branch."""

    pr_number: int
    repo: str


class CancelCodingJob(CommandPayload):
    """Command a Coder to cancel an in-flight coding job."""

    job_id: str
    reason: str


class ReviewPullRequest(CommandPayload):
    """Command the Reviewer to review a pull request."""

    pr_number: int
    repo: str


class CancelReviewJob(CommandPayload):
    """Command the Reviewer to cancel an in-flight review job."""

    job_id: str
    reason: str


class RequestUserInput(CommandPayload):
    """Command the UI to solicit free-form input from a human."""

    session_id: str
    prompt: str


class NotifyUser(CommandPayload):
    """Command the UI to deliver a notification to a human."""

    session_id: str
    message: str


class RequestMerge(CommandPayload):
    """Command a merge of a pull request (issuer: Orchestrator, ID-072)."""

    pr_number: int
    repo: str
