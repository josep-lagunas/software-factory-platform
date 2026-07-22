"""Identity Service persistence layer (ID-058).

Exposes the per-service ``Base`` and the ``User`` / ``UserExternalIdentity``
table models. All Identity ORM tables register against ``Base.metadata``.
"""

from identity.infrastructure.persistence.base import Base
from identity.infrastructure.persistence.models import (
    User,
    UserExternalIdentity,
)

__all__ = ["Base", "User", "UserExternalIdentity"]
