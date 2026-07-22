"""Identity Service persistence models (MAS §9.3, ID-058).

Exposes the ``User`` and ``UserExternalIdentity`` table models. Importing this
module registers both tables on ``Base.metadata`` for Alembic autogenerate.
"""

from identity.infrastructure.persistence.models.user import User
from identity.infrastructure.persistence.models.user_external_identity import (
    UserExternalIdentity,
)

__all__ = ["User", "UserExternalIdentity"]
