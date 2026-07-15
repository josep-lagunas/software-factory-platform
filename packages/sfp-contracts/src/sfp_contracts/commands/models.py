"""The 8 concrete command models (MAS §5.3 / ID-031 / SFP-38).

Each model subclasses :class:`~sfp_contracts.commands.envelope.CommandEnvelope`,
fixes its ``command_type`` to exactly one :class:`CommandType` member (enforced
by the inherited validator) and carries one typed
:mod:`~sfp_contracts.commands.payloads` payload.

``GeneratePRSpecifications`` is deliberately NOT modelled here: it is an
internal Orchestrator operation (MAS §5.3), not an inter-agent command.
"""

from typing import ClassVar

from pydantic import Field

from .envelope import CommandEnvelope, CommandType
from .payloads import (
    CancelCodingJobPayload,
    CancelReviewJobPayload,
    ExecuteCodingJobPayload,
    NotifyUserPayload,
    RequestMergePayload,
    RequestUserInputPayload,
    ReviewPullRequestPayload,
    SynchronizePullRequestPayload,
)


class ExecuteCodingJob(CommandEnvelope):
    """Command a Coder to execute a coding job."""

    EXPECTED_COMMAND_TYPE: ClassVar[CommandType | None] = CommandType.EXECUTE_CODING_JOB
    command_type: CommandType = Field(default=CommandType.EXECUTE_CODING_JOB)
    payload: ExecuteCodingJobPayload


class SynchronizePullRequest(CommandEnvelope):
    """Command a Coder to synchronize (push) a pull request's branch."""

    EXPECTED_COMMAND_TYPE: ClassVar[CommandType | None] = CommandType.SYNCHRONIZE_PULL_REQUEST
    command_type: CommandType = Field(default=CommandType.SYNCHRONIZE_PULL_REQUEST)
    payload: SynchronizePullRequestPayload


class CancelCodingJob(CommandEnvelope):
    """Command a Coder to cancel an in-flight coding job."""

    EXPECTED_COMMAND_TYPE: ClassVar[CommandType | None] = CommandType.CANCEL_CODING_JOB
    command_type: CommandType = Field(default=CommandType.CANCEL_CODING_JOB)
    payload: CancelCodingJobPayload


class ReviewPullRequest(CommandEnvelope):
    """Command the Reviewer to review a pull request."""

    EXPECTED_COMMAND_TYPE: ClassVar[CommandType | None] = CommandType.REVIEW_PULL_REQUEST
    command_type: CommandType = Field(default=CommandType.REVIEW_PULL_REQUEST)
    payload: ReviewPullRequestPayload


class CancelReviewJob(CommandEnvelope):
    """Command the Reviewer to cancel an in-flight review job."""

    EXPECTED_COMMAND_TYPE: ClassVar[CommandType | None] = CommandType.CANCEL_REVIEW_JOB
    command_type: CommandType = Field(default=CommandType.CANCEL_REVIEW_JOB)
    payload: CancelReviewJobPayload


class RequestUserInput(CommandEnvelope):
    """Command the UI to solicit free-form input from a human."""

    EXPECTED_COMMAND_TYPE: ClassVar[CommandType | None] = CommandType.REQUEST_USER_INPUT
    command_type: CommandType = Field(default=CommandType.REQUEST_USER_INPUT)
    payload: RequestUserInputPayload


class NotifyUser(CommandEnvelope):
    """Command the UI to deliver a notification to a human."""

    EXPECTED_COMMAND_TYPE: ClassVar[CommandType | None] = CommandType.NOTIFY_USER
    command_type: CommandType = Field(default=CommandType.NOTIFY_USER)
    payload: NotifyUserPayload


class RequestMerge(CommandEnvelope):
    """Command a merge of a pull request. Issuer: Orchestrator (ID-072)."""

    EXPECTED_COMMAND_TYPE: ClassVar[CommandType | None] = CommandType.REQUEST_MERGE
    command_type: CommandType = Field(default=CommandType.REQUEST_MERGE)
    payload: RequestMergePayload
