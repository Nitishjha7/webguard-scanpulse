"""Tenant-scoped user accounts with role-based access."""
import enum
import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship
from werkzeug.security import check_password_hash, generate_password_hash

from app.models.base import BaseModel


class UserRole(str, enum.Enum):
    ADMIN = "Admin"
    ENGINEER = "Engineer"
    VIEWER = "Viewer"


#: Roles allowed to mutate resources; Viewer is read-only.
WRITE_ROLES = frozenset({UserRole.ADMIN, UserRole.ENGINEER})


class User(BaseModel):
    __tablename__ = "users"

    org_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    email: Mapped[str] = mapped_column(sa.String(255), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        sa.Enum(UserRole, name="user_role"), nullable=False, default=UserRole.ADMIN
    )
    is_active: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)

    organization = relationship("Organization", back_populates="users")

    def set_password(self, raw_password: str) -> None:
        self.password_hash = generate_password_hash(raw_password)

    def check_password(self, raw_password: str) -> bool:
        return check_password_hash(self.password_hash, raw_password)

    @property
    def can_write(self) -> bool:
        return self.role in WRITE_ROLES

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "org_id": str(self.org_id),
            "email": self.email,
            "role": self.role.value,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat(),
        }

    def __repr__(self) -> str:
        return f"<User {self.email} ({self.role.value})>"
