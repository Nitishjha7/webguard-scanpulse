"""Beat dispatchers.

Beat itself only knows one thing: wake up every N seconds. It does not know
per-monitor intervals. These tasks translate "wake up" into "here are the
monitors whose interval has elapsed", and enqueue one probe task per monitor.

The work is split this way so beat stays a fixed-cost tick no matter how many
monitors exist, and so a slow target delays only its own probe.
"""
import logging
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from celery import shared_task

from app.extensions import db
from app.models import Monitor

logger = logging.getLogger(__name__)

#: Deep security scans are expensive and slow-changing — once a day is plenty.
SECURITY_SCAN_INTERVAL = timedelta(hours=24)

#: Never enqueue more than this per tick, so a large tenant cannot starve the queue.
MAX_DISPATCH_PER_TICK = 500


@shared_task(name="webguard.dispatch_due_pings")
def dispatch_due_pings() -> dict:
    """Enqueue an uptime probe for every monitor whose interval has elapsed."""
    from app.tasks.probes import probe_monitor

    now = datetime.now(timezone.utc)
    # Postgres computes the per-row deadline: last_checked_at + interval <= now.
    deadline = now - sa.func.make_interval(0, 0, 0, 0, 0, 0, Monitor.interval_seconds)
    monitors = (
        db.session.query(Monitor)
        .filter(Monitor.is_active.is_(True))
        .filter(sa.or_(Monitor.last_checked_at.is_(None), Monitor.last_checked_at <= deadline))
        .order_by(sa.nullsfirst(Monitor.last_checked_at.asc()))
        .limit(MAX_DISPATCH_PER_TICK)
        .all()
    )

    for monitor in monitors:
        probe_monitor.delay(str(monitor.id))

    if monitors:
        logger.info("dispatched %d uptime probes", len(monitors))
    return {"dispatched": len(monitors)}


@shared_task(name="webguard.dispatch_due_security_scans")
def dispatch_due_security_scans() -> dict:
    """Enqueue the daily SSL + header + DNS scan for every active monitor."""
    from app.tasks.probes import scan_monitor_security, scan_monitor_ssl

    cutoff = datetime.now(timezone.utc) - SECURITY_SCAN_INTERVAL
    monitors = (
        db.session.query(Monitor)
        .filter(Monitor.is_active.is_(True))
        .filter(sa.or_(Monitor.last_scanned_at.is_(None), Monitor.last_scanned_at <= cutoff))
        .order_by(sa.nullsfirst(Monitor.last_scanned_at.asc()))
        .limit(MAX_DISPATCH_PER_TICK)
        .all()
    )

    for monitor in monitors:
        scan_monitor_ssl.delay(str(monitor.id))
        scan_monitor_security.delay(str(monitor.id))

    if monitors:
        logger.info("dispatched %d security scans", len(monitors))
    return {"dispatched": len(monitors)}
