"""Communication Service persistence layer (ID-058).

Exposes the per-service ``Base`` and the ``UserInteraction`` table model. All
Communication ORM tables register against ``Base.metadata``.
"""

from .base import Base
from .user_interaction import UserInteraction

__all__ = ["Base", "UserInteraction"]
