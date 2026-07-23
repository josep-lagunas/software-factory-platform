"""sfp-messaging: the async, transport-agnostic Message Bus (MAS §4.5 / SFP-42).

Re-exports :class:`~sfp_messaging.bus.MessageBus`, the vendor-neutral
publish/subscribe seam every concrete transport (SFP-46, SFP-101) implements.
SFP-43 adds the declarative handler layer above it: the
:func:`~sfp_messaging.decorators.command_handler` /
:func:`~sfp_messaging.decorators.event_handler` decorators and the type-keyed
:class:`~sfp_messaging.registry.HandlerRegistry` they write into. SFP-44 adds
:class:`~sfp_messaging.context.MessageContext` and its contextvars access API
(:func:`~sfp_messaging.context.get_current_context` /
:func:`~sfp_messaging.context.bind_message_context`).
"""

from sfp_messaging.bus import MessageBus as MessageBus
from sfp_messaging.context import MessageContext as MessageContext
from sfp_messaging.context import bind_message_context as bind_message_context
from sfp_messaging.context import get_current_context as get_current_context
from sfp_messaging.decorators import command_handler as command_handler
from sfp_messaging.decorators import event_handler as event_handler
from sfp_messaging.registry import HandlerRegistry as HandlerRegistry
from sfp_messaging.registry import get_default_registry as get_default_registry

__all__: list[str] = [
    "HandlerRegistry",
    "MessageBus",
    "MessageContext",
    "bind_message_context",
    "command_handler",
    "event_handler",
    "get_current_context",
    "get_default_registry",
]

__version__ = "0.1.0"
