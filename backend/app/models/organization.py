"""Tenant root. Every other row in the system hangs off an organization."""
import re

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseModel

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


class Organization(BaseModel):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    slug: Mapped[str] = mapped_column(sa.String(80), nullable=False, unique=True, index=True)

    users = relationship(
        "User", back_populates="organization", cascade="all, delete-orphan", lazy="selectin"
    )
    monitors = relationship(
        "Monitor", back_populates="organization", cascade="all, delete-orphan", lazy="selectin"
    )

    @staticmethod
    def slugify(value: str) -> str:
        return _SLUG_STRIP.sub("-", value.strip().lower()).strip("-") or "org"

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "name": self.name,
            "slug": self.slug,
            "created_at": self.created_at.isoformat(),
        }

    def __repr__(self) -> str:
        return f"<Organization {self.slug}>"
