"""Downsampled uptime history.

Raw probes are the truth for about a week; past that, nobody asks "what was the
latency at 14:23 three months ago". They ask for a daily uptime figure. Keeping
raw rows to answer that is paying storage and scan cost for precision no one
reads, so raw rows condense into hourly buckets, and hourly into daily.

The rollups are ordinary tables refreshed by a beat task rather than TimescaleDB
continuous aggregates — see ``docs/phase-5.md`` for why.
"""
import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db


class _RollupMixin:
    """Columns shared by every rollup grain.

    Percentiles are stored, not just the mean: an average hides the slow tail
    that users actually feel, and it cannot be recovered later once the raw
    rows are gone.
    """

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)

    checks: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    up_checks: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    uptime_percent: Mapped[float] = mapped_column(sa.Float, nullable=False, default=0.0)

    avg_latency_ms: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    min_latency_ms: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    max_latency_ms: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    p95_latency_ms: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    p99_latency_ms: Mapped[float | None] = mapped_column(sa.Float, nullable=True)

    @property
    def down_checks(self) -> int:
        return self.checks - self.up_checks


def _monitor_fk():
    return mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("monitors.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )


def _org_fk():
    return mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )


class PingRollupHourly(_RollupMixin, db.Model):
    """One row per monitor per hour. Raw probes roll into this after 7 days."""

    __tablename__ = "ping_rollups_hourly"
    __table_args__ = (
        # The refresh is an upsert keyed on this, which is what makes a re-run
        # over an already-processed window harmless.
        sa.UniqueConstraint("monitor_id", "bucket", name="uq_rollup_hourly_monitor_bucket"),
        sa.Index("ix_rollup_hourly_org_bucket", "org_id", "bucket"),
    )

    monitor_id: Mapped[uuid.UUID] = _monitor_fk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    bucket: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, index=True
    )

    def to_dict(self) -> dict:
        return {
            "bucket": self.bucket.isoformat(),
            "checks": self.checks,
            "up": self.up_checks,
            "down": self.down_checks,
            "uptime_percent": self.uptime_percent,
            "avg_latency_ms": self.avg_latency_ms,
            "p95_latency_ms": self.p95_latency_ms,
            "max_latency_ms": self.max_latency_ms,
        }


class PingRollupDaily(_RollupMixin, db.Model):
    """One row per monitor per day. Hourly buckets roll into this after 90 days."""

    __tablename__ = "ping_rollups_daily"
    __table_args__ = (
        sa.UniqueConstraint("monitor_id", "bucket", name="uq_rollup_daily_monitor_bucket"),
        sa.Index("ix_rollup_daily_org_bucket", "org_id", "bucket"),
    )

    monitor_id: Mapped[uuid.UUID] = _monitor_fk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    bucket: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, index=True
    )

    def to_dict(self) -> dict:
        return {
            "bucket": self.bucket.date().isoformat(),
            "checks": self.checks,
            "up": self.up_checks,
            "down": self.down_checks,
            "uptime_percent": self.uptime_percent,
            "avg_latency_ms": self.avg_latency_ms,
            "p95_latency_ms": self.p95_latency_ms,
            "max_latency_ms": self.max_latency_ms,
        }
