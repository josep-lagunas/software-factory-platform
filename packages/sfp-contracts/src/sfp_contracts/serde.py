"""Discriminated envelope (de)serialization — typed JSON round-trip (SFP-45).

Closes the gap left open by SFP-219: the envelope's ``payload`` is typed
:data:`~typing.Any` (so :meth:`~sfp_contracts.messages.MessageEnvelope.to_json`
is lossless and
:meth:`~sfp_contracts.messages.MessageEnvelope.from_json` stays functional), but
``from_json`` therefore returns the payload as a plain ``dict``. This module
provides the opt-in *typed* round-trip: :func:`load_command` / :func:`load_event`
read the discriminator off the envelope JSON, resolve the concrete payload class,
and rebuild a fully-typed envelope carrying the concrete payload *instance*
(not a dict).

Grounded in:
- MAS §4.7 — every message is an envelope of routing metadata + a payload; the
  payload half is what this module re-hydrates into its concrete type.
- MAS §5.3 / MAS §5.4 — the command / event catalogues whose discriminators drive
  the dispatch.
- ID-013 — JSON is the reference serialization format.
- ID-031 — the authoritative 8 command names + 11 event names.
- SFP-219 — deferred the typed round-trip to the serde layer (this module).

Design choices (pinned in the SFP-45 PRSpec):
- NON-INVASIVE (R5): the existing ``from_json`` classmethods on
  :class:`~sfp_contracts.messages.MessageEnvelope` /
  :class:`~sfp_contracts.commands.envelope.CommandEnvelope` /
  :class:`~sfp_contracts.events.envelope.EventEnvelope` are left untouched — they
  keep returning generic envelopes with ``dict`` payloads (their documented
  behaviour, exercised by ``tests/messages/test_messages.py``). The typed path is
  opt-in via ``from sfp_contracts.serde import load_command, load_event``.
- EXPLICIT DICT LITERAL mapping (R3): the discriminator->payload-class tables are
  hand-written literals (8 + 11 entries). No introspection, no
  ``SCREAMING_SNAKE``->title-case derivation. This is what avoids the
  ``PR_SPECIFICATIONS_UPDATED``->``PrSpecificationsUpdated`` acronym pitfall
  (R2): the class is ``PRSpecificationsUpdated`` (capital ``PR``), and the
  literal simply maps the enum member to it. The catalogue is fixed by ID-031 /
  MAS §5.3 / §5.4 and changes rarely; a new entry without a mapping surfaces
  immediately in the no-orphan test (TC-10) and as a ``ValidationError``/``KeyError``
  in the round-trip tests.
- ERROR TYPE (TC-05/06/E6): an unknown discriminator, a missing discriminator,
  and malformed JSON all surface as :class:`pydantic.ValidationError` — never a
  custom exception. This happens naturally because the envelope shell is
  validated through pydantic first (its ``command_type`` / ``event_type`` field is
  an :class:`~enum.StrEnum`, which rejects unknown members, and pydantic wraps
  JSON-decode errors).
- ``extra='forbid'`` PRESERVED (TC-07): the concrete payload class is validated
  via ``model_validate`` on the payload sub-document, so an unknown field *inside
  the payload* raises ``ValidationError`` (this only fires because the concrete
  class — not the envelope's ``Any`` — is doing the validating). The envelope
  shell itself already rejects envelope-level extras.
- ANTI-GAMING (R4 / TC-09): the rebuilt envelope's ``payload`` is an instance of
  the concrete payload class, NOT a dict — the core proof that SFP-219's
  ``payload: Any`` gap is closed for the typed path.
- This module is a LEAF consumer: it imports the envelope + payload modules but
  nothing imports it, so there is no import cycle.
"""

from __future__ import annotations

from sfp_contracts.commands.envelope import CommandEnvelope, CommandType
from sfp_contracts.commands.payloads import (
    CancelCodingJob,
    CancelReviewJob,
    CommandPayload,
    ExecuteCodingJob,
    NotifyUser,
    RequestMerge,
    RequestUserInput,
    ReviewPullRequest,
    SynchronizePullRequest,
)
from sfp_contracts.events.envelope import EventEnvelope, EventType
from sfp_contracts.events.payloads import (
    CodingJobUpdated,
    DeploymentUpdated,
    EventPayload,
    ExternalEventReceived,
    MergeUpdated,
    PRSpecificationsUpdated,
    ReviewUpdated,
    TicketUpdated,
    UserInputReceived,
    UserInteractionUpdated,
    UserQueryReceived,
    WorkflowUpdated,
)

#: The discriminator->payload-class table for the 8 commands (ID-031 / MAS §5.3).
#: An EXPLICIT literal (no introspection, no string derivation) so the
#: ``PR_SPECIFICATIONS_UPDATED`` acronym case is mapped by hand, not derived
#: (R2/R3). Exposed as a module attribute so the no-orphan guard (TC-10) can
#: assert ``set(_COMMAND_PAYLOAD_MAP) == set(CommandType)``.
_COMMAND_PAYLOAD_MAP: dict[CommandType, type[CommandPayload]] = {
    CommandType.EXECUTE_CODING_JOB: ExecuteCodingJob,
    CommandType.SYNCHRONIZE_PULL_REQUEST: SynchronizePullRequest,
    CommandType.CANCEL_CODING_JOB: CancelCodingJob,
    CommandType.REVIEW_PULL_REQUEST: ReviewPullRequest,
    CommandType.CANCEL_REVIEW_JOB: CancelReviewJob,
    CommandType.REQUEST_USER_INPUT: RequestUserInput,
    CommandType.NOTIFY_USER: NotifyUser,
    CommandType.REQUEST_MERGE: RequestMerge,
}

#: The discriminator->payload-class table for the 11 events (ID-031 / MAS §5.4).
#: An EXPLICIT literal — note ``EventType.PR_SPECIFICATIONS_UPDATED`` maps to
#: ``PRSpecificationsUpdated`` (capital ``PR``), the case a naive
#: SCREAMING_SNAKE->title-case derivation would get wrong (R2). Exposed as a
#: module attribute for the no-orphan guard (TC-10).
_EVENT_PAYLOAD_MAP: dict[EventType, type[EventPayload]] = {
    EventType.EXTERNAL_EVENT_RECEIVED: ExternalEventReceived,
    EventType.TICKET_UPDATED: TicketUpdated,
    EventType.PR_SPECIFICATIONS_UPDATED: PRSpecificationsUpdated,
    EventType.CODING_JOB_UPDATED: CodingJobUpdated,
    EventType.REVIEW_UPDATED: ReviewUpdated,
    EventType.USER_INPUT_RECEIVED: UserInputReceived,
    EventType.USER_INTERACTION_UPDATED: UserInteractionUpdated,
    EventType.USER_QUERY_RECEIVED: UserQueryReceived,
    EventType.MERGE_UPDATED: MergeUpdated,
    EventType.DEPLOYMENT_UPDATED: DeploymentUpdated,
    EventType.WORKFLOW_UPDATED: WorkflowUpdated,
}


def load_command(data: str | bytes) -> CommandEnvelope:
    """Deserialize a command envelope JSON into a fully-typed :class:`CommandEnvelope`.

    Reads the ``command_type`` discriminator off the envelope, resolves the
    concrete payload class via :data:`_COMMAND_PAYLOAD_MAP`, validates the
    ``payload`` sub-document into that class, and returns an envelope whose
    ``payload`` is the concrete :class:`CommandPayload` instance (not a dict).

    Raises :class:`pydantic.ValidationError` for: malformed JSON, a missing
    ``command_type`` key, an unknown ``command_type`` value, an unknown envelope
    field, an unknown field inside the payload sub-document, or a payload
    sub-document missing a required field.
    """
    # Validate the envelope shell through pydantic first. This is where malformed
    # JSON (TC-E6), a missing command_type (TC-06) and an unknown command_type
    # (TC-05) all surface as ValidationError, and where envelope-level extras are
    # rejected (TC-07). The payload is typed Any, so it round-trips here as a
    # plain dict.
    shell = CommandEnvelope.model_validate_json(data)
    payload_cls = _COMMAND_PAYLOAD_MAP[shell.command_type]
    # Re-validate the payload sub-document into the concrete class. This is where
    # a payload sub-document with an extra field (TC-07) or a missing required
    # field (TC-E3) raises — only the concrete class (not the envelope's Any)
    # enforces the payload schema.
    typed_payload = payload_cls.model_validate(shell.payload)
    # Swap the dict payload for the typed instance. The shell is already a valid
    # envelope; model_copy avoids a redundant full re-validation while yielding a
    # CommandEnvelope carrying the concrete payload instance (TC-09).
    return shell.model_copy(update={"payload": typed_payload})


def load_event(data: str | bytes) -> EventEnvelope:
    """Deserialize an event envelope JSON into a fully-typed :class:`EventEnvelope`.

    Reads the ``event_type`` discriminator off the envelope, resolves the
    concrete payload class via :data:`_EVENT_PAYLOAD_MAP` (note
    ``PR_SPECIFICATIONS_UPDATED`` resolves to ``PRSpecificationsUpdated``), and
    returns an envelope whose ``payload`` is the concrete :class:`EventPayload`
    instance (not a dict).

    Raises :class:`pydantic.ValidationError` for: malformed JSON, a missing
    ``event_type`` key, an unknown ``event_type`` value, an unknown envelope
    field, an unknown field inside the payload sub-document, or a payload
    sub-document missing a required field.
    """
    shell = EventEnvelope.model_validate_json(data)
    payload_cls = _EVENT_PAYLOAD_MAP[shell.event_type]
    typed_payload = payload_cls.model_validate(shell.payload)
    return shell.model_copy(update={"payload": typed_payload})
