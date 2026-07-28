"""Orchestrator Service persistence layer (ID-058).

Exposes the per-service ``Base``. ORM table models are downstream
(SFP-83..88) and do not exist yet; this package will re-export them as they
land. All Orchestrator ORM tables register against ``Base.metadata``.
"""

from orchestrator.infrastructure.persistence.base import Base

__all__ = ["Base"]
