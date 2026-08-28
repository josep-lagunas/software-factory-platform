"""The Orchestrator application layer: command emitters and their seams.

The application layer turns Orchestrator *decisions* into inter-agent commands
(MAS §5.3 / ID-072): it constructs the command envelope and publishes it on
the injected bus. It performs no state transition of its own (MAS §8.6) —
commands carry intent; the workflow advances only on events and user
decisions.
"""

from orchestrator.application.command_emitters import ExecuteCodingJobEmitter

__all__ = ["ExecuteCodingJobEmitter"]
