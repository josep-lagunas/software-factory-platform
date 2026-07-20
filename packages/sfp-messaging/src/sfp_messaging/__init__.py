"""sfp-messaging: the async, transport-agnostic Message Bus (MAS §4.5 / SFP-42).

Re-exports :class:`~sfp_messaging.bus.MessageBus`, the vendor-neutral
publish/subscribe seam every concrete transport (SFP-46, SFP-101) implements.
"""

from sfp_messaging.bus import MessageBus as MessageBus

__version__ = "0.1.0"
