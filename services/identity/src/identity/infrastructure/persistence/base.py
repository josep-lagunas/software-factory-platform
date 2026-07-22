"""Per-service SQLAlchemy declarative base for the Identity Service.

Grounded in ID-058: each service owns its own ``Base`` (there is no shared
cross-service Base) and ORM table classes live in ``infrastructure/persistence/``.
Every service database — here ``sfp_identity`` — is an independent ownership
boundary (AP-001, MAS §7.9), so this ``Base`` registers Identity tables only.
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base for all Identity Service persistence models.

    All Identity ORM tables register against this ``Base.metadata``, which is
    also the Alembic ``target_metadata`` (see ``migrations/env.py``).
    """
