"""Probe result tables: uptime pings, SSL scans and security audits.

Every row carries ``org_id`` alongside ``monitor_id``. That is deliberate
denormalization: it lets ``tenant_query`` filter these high-volume tables
without joining back to ``monitors`` on every dashboard read, and it gives
Phase 5 a tenant key to partition on.
"""
import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import BaseModel, utcnow

#: JSONB on Postgres, plain JSON on SQLite so the test config still works.
JSONColumn = sa.JSON().with_variant(JSONB, "postgresql")


def _org_fk():
    return mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )


def _monitor_fk():
    return mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("monitors.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )


class PingLog(db.Model):
    """One uptime probe. The highest-volume table in the system — BIGINT
    identity rather than a UUID, and no updated_at, since rows are append-only.
    """

    __tablename__ = "ping_logs"
    __table_args__ = (
        sa.Index("ix_ping_logs_monitor_checked", "monitor_id", "checked_at"),
        sa.Index("ix_ping_logs_org_checked", "org_id", "checked_at"),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    monitor_id: Mapped[uuid.UUID] = _monitor_fk()
    org_id: Mapped[uuid.UUID] = _org_fk()

    region: Mapped[str] = mapped_column(sa.String(32), nullable=False, default="default")
    status_code: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    latency_ms: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    is_up: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    error: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    checked_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, default=utcnow
    )

    monitor = relationship("Monitor", back_populates="ping_logs")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "monitor_id": str(self.monitor_id),
            "region": self.region,
            "status_code": self.status_code,
            "latency_ms": self.latency_ms,
            "is_up": self.is_up,
            "error": self.error,
            "checked_at": self.checked_at.isoformat(),
        }


class SslScan(BaseModel):
    """Result of one TLS handshake + certificate decode."""

    __tablename__ = "ssl_scans"
    __table_args__ = (
        sa.Index("ix_ssl_scans_monitor_created", "monitor_id", "created_at"),
        sa.Index("ix_ssl_scans_days_left", "days_left"),
    )

    monitor_id: Mapped[uuid.UUID] = _monitor_fk()
    org_id: Mapped[uuid.UUID] = _org_fk()

    issuer: Mapped[str | None] = mapped_column(sa.String(512), nullable=True)
    subject: Mapped[str | None] = mapped_column(sa.String(512), nullable=True)
    valid_from: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    valid_to: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    days_left: Mapped[int | None] = mapped_column(sa.Integer, nullable=True, index=True)
    tls_version: Mapped[str | None] = mapped_column(sa.String(16), nullable=True)
    cipher: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    is_valid: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    verify_error: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    error: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    payload: Mapped[dict | None] = mapped_column(JSONColumn, nullable=True)

    monitor = relationship("Monitor", back_populates="ssl_scans")

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "monitor_id": str(self.monitor_id),
            "issuer": self.issuer,
            "subject": self.subject,
            "valid_from": self.valid_from.isoformat() if self.valid_from else None,
            "valid_to": self.valid_to.isoformat() if self.valid_to else None,
            "days_left": self.days_left,
            "tls_version": self.tls_version,
            "cipher": self.cipher,
            "is_valid": self.is_valid,
            "verify_error": self.verify_error,
            "error": self.error,
            "scanned_at": self.created_at.isoformat(),
        }


class SecurityAudit(BaseModel):
    """Combined HTTP-header and DNS-posture audit for one monitor.

    ``open_ports`` stays null until the Phase 4 port scanner fills it.
    """

    __tablename__ = "security_audits"
    __table_args__ = (
        sa.Index("ix_security_audits_monitor_created", "monitor_id", "created_at"),
    )

    monitor_id: Mapped[uuid.UUID] = _monitor_fk()
    org_id: Mapped[uuid.UUID] = _org_fk()

    score: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    grade: Mapped[str] = mapped_column(sa.String(2), nullable=False, default="F")
    dns_score: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    dns_grade: Mapped[str | None] = mapped_column(sa.String(2), nullable=True)

    headers_payload: Mapped[dict | None] = mapped_column(JSONColumn, nullable=True)
    dns_payload: Mapped[dict | None] = mapped_column(JSONColumn, nullable=True)
    open_ports: Mapped[dict | None] = mapped_column(JSONColumn, nullable=True)
    error: Mapped[str | None] = mapped_column(sa.Text, nullable=True)

    monitor = relationship("Monitor", back_populates="security_audits")

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "monitor_id": str(self.monitor_id),
            "score": self.score,
            "grade": self.grade,
            "dns_score": self.dns_score,
            "dns_grade": self.dns_grade,
            "headers": self.headers_payload,
            "dns": self.dns_payload,
            "open_ports": self.open_ports,
            "error": self.error,
            "scanned_at": self.created_at.isoformat(),
        }
