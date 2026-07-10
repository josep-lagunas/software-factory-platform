"""Per-service SQLAlchemy declarative ``Base`` (ID-058).

Each service owns its own logical database (``sfp_external_events``) and its
own ``Base``; there is no shared cross-service declarative base, matching the
ownership boundaries of AP-001 / MAS §7.9 (no cross-service foreign keys).
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base for all External Events persistence models.

    All ORM-mapped tables for this service declare ``Base`` as their registry
    parent so Alembic autogenerate can find the complete ``Base.metadata``.
    """
