"""Per-tenant alert destinations."""
import enum
import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseModel
from app.models.scans import JSONColumn


class ChannelType(str, enum.Enum):
    SLACK = "slack"
    DISCORD = "discord"
    EMAIL = "email"
    WEBHOOK = "webhook"


class AlertEvent(str, enum.Enum):
    INCIDENT_OPENED = "incident.opened"
    INCIDENT_RESOLVED = "incident.resolved"
    SSL_EXPIRING = "ssl.expiring"
    SSL_INVALID = "ssl.invalid"


DEFAULT_EVENTS = [e.value for e in AlertEvent]


class NotificationChannel(BaseModel):
    __tablename__ = "notification_channels"
    __table_args__ = (
        sa.UniqueConstraint("org_id", "type", "target", name="uq_channel_org_type_target"),
        sa.Index("ix_notification_channels_org_active", "org_id", "is_active"),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    type: Mapped[ChannelType] = mapped_column(
        sa.Enum(ChannelType, name="channel_type"), nullable=False
    )
    #: Webhook URL for slack/discord/webhook, recipient address for email.
    target: Mapped[str] = mapped_column(sa.String(1024), nullable=False)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)

    #: Which events this channel wants. Empty/null means all of them.
    events: Mapped[list | None] = mapped_column(JSONColumn, nullable=True)

    last_used_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    failure_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)

    organization = relationship("Organization", back_populates="notification_channels")

    def wants(self, event: AlertEvent) -> bool:
        return not self.events or event.value in self.events

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "org_id": str(self.org_id),
            "name": self.name,
            "type": self.type.value,
            # Webhook URLs embed their own credential, so never echo one back.
            "target": _mask(self.type, self.target),
            "is_active": self.is_active,
            "events": self.events or DEFAULT_EVENTS,
            "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None,
            "last_error": self.last_error,
            "failure_count": self.failure_count,
            "created_at": self.created_at.isoformat(),
        }

    def __repr__(self) -> str:
        return f"<NotificationChannel {self.type.value} {self.name}>"


def _mask(channel_type: ChannelType, target: str) -> str:
    if channel_type is ChannelType.EMAIL:
        return target
    return f"{target[:32]}…" if len(target) > 32 else target
