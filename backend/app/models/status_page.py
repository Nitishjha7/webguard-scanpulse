"""Public status pages.

These are the only pages in the system served without authentication, so what
they expose is a deliberate, narrow choice rather than whatever a model happens
to carry. Monitor *URLs* in particular stay hidden unless the tenant opts in —
a status page that leaks internal hostnames is a reconnaissance gift.
"""
import re
import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseModel
from app.models.scans import JSONColumn

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")

#: Slugs that would collide with our own routes.
RESERVED_SLUGS = frozenset({"api", "health", "static", "admin", "status", "www"})


class StatusPage(BaseModel):
    __tablename__ = "status_pages"
    __table_args__ = (
        sa.UniqueConstraint("org_id", "name", name="uq_status_page_org_name"),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    slug: Mapped[str] = mapped_column(sa.String(80), nullable=False, unique=True, index=True)
    #: A CNAME the tenant points at us, matched on the Host header.
    custom_domain: Mapped[str | None] = mapped_column(
        sa.String(255), nullable=True, unique=True, index=True
    )

    headline: Mapped[str | None] = mapped_column(sa.String(200), nullable=True)
    description: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    support_url: Mapped[str | None] = mapped_column(sa.String(2048), nullable=True)

    is_published: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    #: Days of uptime history shown in the bar chart.
    history_days: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=90)

    #: Opt-in disclosures. Both default off: a public page should say *whether*
    #: a service is up, not where it lives or how fast it answered.
    show_urls: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    show_latency: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    show_incidents: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)

    #: Ordered monitor ids. A list rather than a join table because ordering is
    #: the only relationship attribute, and the tenant controls it directly.
    monitor_ids: Mapped[list | None] = mapped_column(JSONColumn, nullable=True)

    organization = relationship("Organization", back_populates="status_pages")

    @staticmethod
    def slugify(value: str) -> str:
        return _SLUG_STRIP.sub("-", value.strip().lower()).strip("-") or "status"

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "org_id": str(self.org_id),
            "name": self.name,
            "slug": self.slug,
            "custom_domain": self.custom_domain,
            "headline": self.headline,
            "description": self.description,
            "support_url": self.support_url,
            "is_published": self.is_published,
            "history_days": self.history_days,
            "show_urls": self.show_urls,
            "show_latency": self.show_latency,
            "show_incidents": self.show_incidents,
            "monitor_ids": self.monitor_ids or [],
            "public_path": f"/status/{self.slug}",
            "created_at": self.created_at.isoformat(),
        }

    def __repr__(self) -> str:
        return f"<StatusPage {self.slug}>"
