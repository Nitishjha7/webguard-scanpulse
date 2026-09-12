"""Scheduled database maintenance: partitions, rollups and retention.

The ordering inside :func:`run_retention` is deliberate and load-bearing — raw
probes are only ever deleted after they have been rolled up.
"""
import logging
from datetime import datetime, timedelta, timezone

from celery import shared_task
from flask import current_app

from app.extensions import db
from app.services import partitions, rollups

logger = logging.getLogger(__name__)


@shared_task(name="webguard.ensure_partitions")
def ensure_partitions() -> dict:
    """Keep future ``ping_logs`` partitions ready.

    Runs daily even though it only matters monthly: a probe that fails because
    next month's partition is missing would be recorded as a site outage, so
    the headroom is worth the near-zero cost of a no-op run.
    """
    if not partitions.is_partitioned():
        return {"skipped": "ping_logs is not partitioned"}

    created = partitions.ensure_partitions()
    partitions.ensure_default_partition()
    db.session.commit()

    stranded = partitions.default_partition_rows()
    if stranded:
        # Postgres refuses to create a partition overlapping rows already in the
        # default, so this will eventually break partition creation outright.
        logger.error(
            "%d rows are stranded in %s — future partition creation will fail",
            stranded,
            partitions.DEFAULT_PARTITION,
        )

    return {"partitions": created, "default_partition_rows": stranded}


@shared_task(name="webguard.refresh_rollups")
def refresh_rollups() -> dict:
    """Bring the hourly and daily rollups up to date."""
    hourly = rollups.refresh_hourly()
    daily = rollups.refresh_daily()
    return {"hourly_buckets": hourly, "daily_buckets": daily}


@shared_task(name="webguard.run_retention")
def run_retention() -> dict:
    """Condense, then delete. Never the other way round.

    A backfill runs first so that anything about to fall out of the raw window
    is guaranteed to exist in the hourly table before the partition holding it
    is dropped. Losing history to a retention job is not recoverable.
    """
    raw_days = current_app.config["RAW_RETENTION_DAYS"]
    hourly_days = current_app.config["HOURLY_RETENTION_DAYS"]

    backfilled = rollups.backfill_hourly(days=raw_days + 1)
    daily = rollups.refresh_daily(
        since=datetime.now(timezone.utc) - timedelta(days=hourly_days + 1)
    )

    dropped = []
    if partitions.is_partitioned():
        cutoff = (datetime.now(timezone.utc) - timedelta(days=raw_days)).date()
        # Whole months only. A partition is dropped once *every* row inside it
        # is older than the cutoff, so the effective raw window is longer than
        # the nominal one — the alternative is deleting rows we promised to keep.
        dropped = partitions.drop_partitions_before(cutoff.replace(day=1))

    pruned = rollups.prune_hourly_rollups(retention_days=hourly_days)

    result = {
        "hourly_backfilled": backfilled,
        "daily_refreshed": daily,
        "partitions_dropped": dropped,
        "hourly_rows_pruned": pruned,
    }
    logger.info("retention pass: %s", result)
    return result
