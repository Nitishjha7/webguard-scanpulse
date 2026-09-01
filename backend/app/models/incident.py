"""Incident lifecycle: one row per outage, opened and closed by the state machine."""
import enum
import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseModel
from app.models.scans import JSONColumn


class IncidentStatus(str, enum.Enum):
    DOWN = "DOWN"
    DEGRADED = "DEGRADED"
    RESOLVED = "RESOLVED"


#: An incident in either of these states is still open.
OPEN_STATUSES = frozenset({IncidentStatus.DOWN, IncidentStatus.DEGRADED})


class Incident(BaseModel):
    __tablename__ = "incidents"
    __table_args__ = (
        sa.Index("ix_incidents_monitor_started", "monitor_id", "started_at"),
        sa.Index("ix_incidents_org_status", "org_id", "status"),
        # At most one open incident per monitor. The state machine relies on this:
        # a race between two workers hits a constraint violation, not a duplicate.
        sa.Index(
            "uq_incidents_one_open_per_monitor",
            "monitor_id",
            unique=True,
            postgresql_where=sa.text("resolved_at IS NULL"),
        ),
    )

    monitor_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("monitors.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    status: Mapped[IncidentStatus] = mapped_column(
        sa.Enum(IncidentStatus, name="incident_status"), nullable=False, index=True
    )
    started_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    root_cause: Mapped[str | None] = mapped_column(sa.Text, nullable=True)

    #: Failed probes observed before the incident opened — the quorum evidence.
    failure_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    #: Distinct regions that observed the failure, for multi-region quorum.
    regions: Mapped[dict | None] = mapped_column(JSONColumn, nullable=True)
    #: Append-only record of which channels were notified, and when.
    notifications: Mapped[dict | None] = mapped_column(JSONColumn, nullable=True)

    monitor = relationship("Monitor", back_populates="incidents")

    @property
    def is_open(self) -> bool:
        return self.resolved_at is None

    @property
    def duration_seconds(self) -> float | None:
        if self.resolved_at is None:
            return None
        return (self.resolved_at - self.started_at).total_seconds()

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "monitor_id": str(self.monitor_id),
            "status": self.status.value,
            "started_at": self.started_at.isoformat(),
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
            "duration_seconds": self.duration_seconds,
            "is_open": self.is_open,
            "root_cause": self.root_cause,
            "failure_count": self.failure_count,
            "regions": self.regions,
            "notifications": self.notifications,
        }

    def __repr__(self) -> str:
        return f"<Incident {self.status.value} monitor={self.monitor_id}>"
