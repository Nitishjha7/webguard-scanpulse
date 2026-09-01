"""Model package. Importing this registers every table on the SQLAlchemy metadata,
which is what Alembic autogenerate reads.
"""
from app.models.base import BaseModel, TimestampMixin, UUIDPrimaryKeyMixin, utcnow
from app.models.monitor import Monitor
from app.models.organization import Organization
from app.models.user import WRITE_ROLES, User, UserRole

__all__ = [
    "BaseModel",
    "TimestampMixin",
    "UUIDPrimaryKeyMixin",
    "utcnow",
    "Organization",
    "User",
    "UserRole",
    "WRITE_ROLES",
    "Monitor",
]
