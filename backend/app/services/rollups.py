"""Downsampling: raw probes -> hourly buckets -> daily buckets.

Every refresh is an idempotent upsert over a bounded time window, keyed on
``(monitor_id, bucket)``. That property is what makes the maintenance task safe
to retry, safe to run twice, and safe to run after a gap — a re-run over an
already-processed window recomputes the same numbers and writes them again.

The aggregation itself happens in Postgres. Pulling a month of raw probes into
Python to average them would move gigabytes over the wire to produce a few
hundred rows.
"""
import logging
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.extensions import db
from app.models import PingRollupDaily, PingRollupHourly

logger = logging.getLogger(__name__)

#: Raw probes are kept at full resolution for this long, then condensed.
RAW_RETENTION_DAYS = 7

#: Hourly buckets are kept this long before being condensed into daily ones.
HOURLY_RETENTION_DAYS = 90

#: Overlap re-processed on every run. A probe can be written a moment after the
#: bucket it belongs to has already been rolled up; recomputing a little extra
#: is far cheaper than a permanently wrong figure.
REFRESH_OVERLAP_HOURS = 2


def _aggregate_columns(timestamp_column, latency_column, up_column, count_column):
    """Aggregate expressions shared by both grains."""
    return [
        sa.func.count(count_column).label("checks"),
        sa.func.count(sa.case((up_column.is_(True), 1))).label("up_checks"),
        sa.func.avg(latency_column).label("avg_latency_ms"),
        sa.func.min(latency_column).label("min_latency_ms"),
        sa.func.max(latency_column).label("max_latency_ms"),
        sa.func.percentile_cont(0.95)
        .within_group(latency_column.asc())
        .label("p95_latency_ms"),
        sa.func.percentile_cont(0.99)
        .within_group(latency_column.asc())
        .label("p99_latency_ms"),
    ]


def _upsert(model, rows: list[dict]) -> int:
    """Insert or update rollup rows, keyed on (monitor_id, bucket)."""
    if not rows:
        return 0

    statement = pg_insert(model.__table__).values(rows)
    updatable = {
        column: statement.excluded[column]
        for column in (
            "checks",
            "up_checks",
            "uptime_percent",
            "avg_latency_ms",
            "min_latency_ms",
            "max_latency_ms",
            "p95_latency_ms",
            "p99_latency_ms",
        )
    }
    db.session.execute(
        statement.on_conflict_do_update(
            index_elements=["monitor_id", "bucket"], set_=updatable
        )
    )
    return len(rows)


def _to_rows(result) -> list[dict]:
    rows = []
    for record in result:
        checks = record.checks or 0
        up = record.up_checks or 0
        rows.append(
            {
                "monitor_id": record.monitor_id,
                "org_id": record.org_id,
                "bucket": record.bucket,
                "checks": checks,
                "up_checks": up,
                "uptime_percent": round(up / checks * 100, 4) if checks else 0.0,
                "avg_latency_ms": _round(record.avg_latency_ms),
                "min_latency_ms": _round(record.min_latency_ms),
                "max_latency_ms": _round(record.max_latency_ms),
                "p95_latency_ms": _round(record.p95_latency_ms),
                "p99_latency_ms": _round(record.p99_latency_ms),
            }
        )
    return rows


def _round(value) -> float | None:
    return round(float(value), 2) if value is not None else None


def refresh_hourly(since: datetime | None = None, until: datetime | None = None) -> int:
    """Roll raw ``ping_logs`` into hourly buckets."""
    from app.models import PingLog

    now = datetime.now(timezone.utc)
    until = until or now
    since = since or (until - timedelta(hours=REFRESH_OVERLAP_HOURS))

    bucket = sa.func.date_trunc("hour", PingLog.checked_at).label("bucket")
    result = db.session.execute(
        sa.select(
            PingLog.monitor_id,
            PingLog.org_id,
            bucket,
            *_aggregate_columns(
                PingLog.checked_at, PingLog.latency_ms, PingLog.is_up, PingLog.id
            ),
        )
        .where(PingLog.checked_at >= since, PingLog.checked_at < until)
        .group_by(PingLog.monitor_id, PingLog.org_id, bucket)
    )

    written = _upsert(PingRollupHourly, _to_rows(result))
    db.session.commit()
    if written:
        logger.info("rolled up %d hourly buckets (%s -> %s)", written, since, until)
    return written


def refresh_daily(since: datetime | None = None, until: datetime | None = None) -> int:
    """Roll hourly buckets into daily ones.

    Aggregating the hourly table rather than raw probes keeps this cheap, and
    keeps daily numbers available after the raw rows are gone. The cost is that
    a daily average is an average of hourly averages, which is only exact when
    every hour holds the same number of checks. Hourly ``checks`` is used as a
    weight so the uptime figure stays exact; the latency mean is approximate by
    construction, which is acceptable for a 90-day-old number.
    """
    now = datetime.now(timezone.utc)
    until = until or now
    since = since or (until - timedelta(days=2))

    bucket = sa.func.date_trunc("day", PingRollupHourly.bucket).label("bucket")
    result = db.session.execute(
        sa.select(
            PingRollupHourly.monitor_id,
            PingRollupHourly.org_id,
            bucket,
            sa.func.sum(PingRollupHourly.checks).label("checks"),
            sa.func.sum(PingRollupHourly.up_checks).label("up_checks"),
            # Weighted by the number of checks in each hour, so a quiet hour
            # does not count as much as a busy one.
            (
                sa.func.sum(PingRollupHourly.avg_latency_ms * PingRollupHourly.checks)
                / sa.func.nullif(sa.func.sum(PingRollupHourly.checks), 0)
            ).label("avg_latency_ms"),
            sa.func.min(PingRollupHourly.min_latency_ms).label("min_latency_ms"),
            sa.func.max(PingRollupHourly.max_latency_ms).label("max_latency_ms"),
            # The max of the hourly p95s: a true daily percentile is not
            # recoverable from bucketed data, and the worst hour is the number
            # an operator actually wants.
            sa.func.max(PingRollupHourly.p95_latency_ms).label("p95_latency_ms"),
            sa.func.max(PingRollupHourly.p99_latency_ms).label("p99_latency_ms"),
        )
        .where(PingRollupHourly.bucket >= since, PingRollupHourly.bucket < until)
        .group_by(PingRollupHourly.monitor_id, PingRollupHourly.org_id, bucket)
    )

    written = _upsert(PingRollupDaily, _to_rows(result))
    db.session.commit()
    if written:
        logger.info("rolled up %d daily buckets (%s -> %s)", written, since, until)
    return written


def backfill_hourly(days: int = RAW_RETENTION_DAYS) -> int:
    """Roll up everything in the raw retention window.

    Used after a gap in the schedule, and once by the migration so existing
    history is not lost the first time retention runs.
    """
    until = datetime.now(timezone.utc)
    return refresh_hourly(since=until - timedelta(days=days), until=until)


def prune_hourly_rollups(retention_days: int = HOURLY_RETENTION_DAYS) -> int:
    """Delete hourly buckets already represented in the daily table."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    deleted = (
        db.session.query(PingRollupHourly)
        .filter(PingRollupHourly.bucket < cutoff)
        .delete(synchronize_session=False)
    )
    db.session.commit()
    if deleted:
        logger.info("pruned %d hourly rollup rows older than %s", deleted, cutoff.date())
    return deleted
