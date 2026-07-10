"""Per-service SQLAlchemy declarative base for the Communication Service.

Grounded in ID-058: each service owns its own ``Base`` (there is no shared
cross-service Base) and ORM table classes live in ``infrastructure/persistence/``.
Every service database — here ``sfp_communication`` — is an independent
ownership boundary (AP-001, MAS §7.9), so this ``Base`` registers Communication
tables only.
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base for all Communication Service persistence models.

    All Communication ORM tables register against this ``Base.metadata``, which
    is also the Alembic ``target_metadata`` (see ``alembic/env.py``).
    """
