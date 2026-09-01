"""A monitored target URL owned by exactly one organization."""
import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import BaseModel


class Monitor(BaseModel):
    __tablename__ = "monitors"
    __table_args__ = (
        sa.CheckConstraint("interval_seconds >= 30", name="ck_monitors_min_interval"),
        sa.CheckConstraint("timeout_seconds > 0", name="ck_monitors_positive_timeout"),
        sa.UniqueConstraint("org_id", "url", name="uq_monitors_org_url"),
        sa.Index("ix_monitors_active_scheduling", "is_active", "interval_seconds"),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    url: Mapped[str] = mapped_column(sa.String(2048), nullable=False)
    interval_seconds: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=300)
    timeout_seconds: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=10)
    is_active: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=True, index=True
    )

    #: Advanced by the ping task. The beat dispatcher reads this to decide which
    #: monitors are due, so it never has to aggregate over ping_logs.
    last_checked_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    last_scanned_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )

    # --- Quorum state -----------------------------------------------------
    #
    # Counters rather than an aggregate over ping_logs: the state machine runs
    # on every single probe, and scanning the highest-volume table each time
    # would make uptime checks scale with history length.
    consecutive_failures: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=0, server_default="0"
    )
    consecutive_degraded: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=0, server_default="0"
    )
    #: Failed probes required before an incident opens. 2 is the anti-flapping
    #: default from the spec - one blip must never page anyone.
    failure_threshold: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=2, server_default="2"
    )
    #: Latency above this marks the monitor DEGRADED rather than DOWN.
    #: Null disables degraded detection for this monitor.
    degraded_latency_ms: Mapped[float | None] = mapped_column(sa.Float, nullable=True)

    organization = relationship("Organization", back_populates="monitors")
    ping_logs = relationship(
        "PingLog", back_populates="monitor", cascade="all, delete-orphan", passive_deletes=True
    )
    ssl_scans = relationship(
        "SslScan", back_populates="monitor", cascade="all, delete-orphan", passive_deletes=True
    )
    security_audits = relationship(
        "SecurityAudit", back_populates="monitor", cascade="all, delete-orphan", passive_deletes=True
    )
    incidents = relationship(
        "Incident", back_populates="monitor", cascade="all, delete-orphan", passive_deletes=True
    )

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "org_id": str(self.org_id),
            "name": self.name,
            "url": self.url,
            "interval_seconds": self.interval_seconds,
            "timeout_seconds": self.timeout_seconds,
            "is_active": self.is_active,
            "last_checked_at": self.last_checked_at.isoformat() if self.last_checked_at else None,
            "last_scanned_at": self.last_scanned_at.isoformat() if self.last_scanned_at else None,
            "consecutive_failures": self.consecutive_failures,
            "failure_threshold": self.failure_threshold,
            "degraded_latency_ms": self.degraded_latency_ms,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    def __repr__(self) -> str:
        return f"<Monitor {self.name} {self.url}>"
