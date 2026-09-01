"""Incident state machine with anti-flapping quorum.

The rule the whole thing exists to enforce: **one failed probe is not an
outage.** Networks blip. A monitor must fail ``failure_threshold`` consecutive
probes (default 2) before an incident opens and anyone is paged.

State transitions, evaluated once per probe::

    UP        --(N consecutive failures)-->  DOWN
    UP        --(N consecutive slow)------>  DEGRADED
    DEGRADED  --(N consecutive failures)-->  DOWN       (escalation)
    DOWN      --(one success, slow)------->  DEGRADED   (de-escalation)
    DOWN      --(one success, fast)------->  RESOLVED
    DEGRADED  --(one success, fast)------->  RESOLVED

Recovery is deliberately asymmetric: opening needs a quorum, closing needs a
single success. A flapping target should be reported recovered promptly and
re-opened if it fails again, rather than held DOWN while it serves traffic.
"""
import logging
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Incident, IncidentStatus, Monitor

logger = logging.getLogger(__name__)


def open_incident_for(monitor: Monitor) -> Incident | None:
    return (
        db.session.query(Incident)
        .filter(Incident.monitor_id == monitor.id, Incident.resolved_at.is_(None))
        .order_by(Incident.started_at.desc())
        .first()
    )


def evaluate(monitor: Monitor, probe: dict) -> dict:
    """Fold one probe result into the monitor's incident state.

    Returns ``{"transition": ..., "incident_id": ...}``. ``transition`` is None
    when nothing changed, which is the overwhelmingly common case.

    The caller owns the transaction — this stages changes and never commits, so
    the ping row and the state change land together or not at all.
    """
    now = datetime.now(timezone.utc)
    incident = open_incident_for(monitor)

    if not probe.get("is_up"):
        return _handle_failure(monitor, probe, incident, now)
    return _handle_success(monitor, probe, incident, now)


def _handle_failure(monitor, probe, incident, now) -> dict:
    monitor.consecutive_failures += 1
    monitor.consecutive_degraded = 0

    reason = probe.get("error") or f"HTTP {probe.get('status_code')}"
    region = probe.get("region", "default")

    if incident is not None and incident.status is IncidentStatus.DOWN:
        # Already down — just accumulate evidence.
        incident.failure_count += 1
        incident.regions = _add_region(incident.regions, region)
        return {"transition": None, "incident_id": str(incident.id)}

    if monitor.consecutive_failures < monitor.failure_threshold:
        logger.info(
            "monitor %s failure %d/%d — below quorum, no incident",
            monitor.name,
            monitor.consecutive_failures,
            monitor.failure_threshold,
        )
        return {"transition": None, "incident_id": None, "below_quorum": True}

    if incident is not None:
        # DEGRADED escalating to DOWN — same incident, harder status.
        incident.status = IncidentStatus.DOWN
        incident.root_cause = reason
        incident.failure_count += 1
        incident.regions = _add_region(incident.regions, region)
        logger.warning("monitor %s escalated DEGRADED -> DOWN: %s", monitor.name, reason)
        return {"transition": "escalated", "status": "DOWN", "incident_id": str(incident.id)}

    incident = _create_incident(monitor, IncidentStatus.DOWN, reason, now, region)
    if incident is None:
        return {"transition": None, "incident_id": None, "raced": True}

    logger.warning("monitor %s DOWN: %s", monitor.name, reason)
    return {"transition": "opened", "status": "DOWN", "incident_id": str(incident.id)}


def _handle_success(monitor, probe, incident, now) -> dict:
    monitor.consecutive_failures = 0

    threshold = monitor.degraded_latency_ms
    latency = probe.get("latency_ms")
    is_slow = threshold is not None and latency is not None and latency > threshold

    if is_slow:
        monitor.consecutive_degraded += 1
        reason = f"latency {latency}ms exceeds {threshold}ms threshold"

        if incident is not None:
            if incident.status is IncidentStatus.DOWN:
                # The target is answering again, just slowly. Holding it DOWN
                # would be wrong and — because every later slow probe takes this
                # same branch — the incident would never clear at all.
                incident.status = IncidentStatus.DEGRADED
                incident.root_cause = reason
                logger.warning(
                    "monitor %s de-escalated DOWN -> DEGRADED: %s", monitor.name, reason
                )
                return {
                    "transition": "deescalated",
                    "status": "DEGRADED",
                    "incident_id": str(incident.id),
                }
            # Already DEGRADED; another slow probe adds nothing new.
            return {"transition": None, "incident_id": str(incident.id)}
        if monitor.consecutive_degraded < monitor.failure_threshold:
            return {"transition": None, "incident_id": None, "below_quorum": True}

        incident = _create_incident(
            monitor, IncidentStatus.DEGRADED, reason, now, probe.get("region", "default")
        )
        if incident is None:
            return {"transition": None, "incident_id": None, "raced": True}
        logger.warning("monitor %s DEGRADED: %s", monitor.name, reason)
        return {"transition": "opened", "status": "DEGRADED", "incident_id": str(incident.id)}

    monitor.consecutive_degraded = 0

    if incident is None:
        return {"transition": None, "incident_id": None}

    incident.resolved_at = now
    incident.status = IncidentStatus.RESOLVED
    logger.info("monitor %s RESOLVED after %.0fs", monitor.name, incident.duration_seconds or 0)
    return {
        "transition": "resolved",
        "status": "RESOLVED",
        "incident_id": str(incident.id),
        "duration_seconds": incident.duration_seconds,
    }


def _create_incident(monitor, status, reason, now, region) -> Incident | None:
    """Insert a new open incident, tolerating a concurrent worker winning the race.

    The partial unique index on ``(monitor_id) WHERE resolved_at IS NULL`` is
    what makes this safe: the loser gets an IntegrityError instead of a
    duplicate incident and a duplicate page.
    """
    incident = Incident(
        monitor_id=monitor.id,
        org_id=monitor.org_id,
        status=status,
        started_at=now,
        root_cause=reason,
        failure_count=monitor.consecutive_failures or monitor.consecutive_degraded,
        regions=[region],
        notifications=[],
    )
    db.session.add(incident)
    try:
        db.session.flush()
    except IntegrityError:
        db.session.rollback()
        logger.info("monitor %s: incident already opened by another worker", monitor.name)
        return None
    return incident


def _add_region(existing, region: str) -> list:
    regions = list(existing or [])
    if region not in regions:
        regions.append(region)
    return regions


def resolve_manually(incident: Incident, note: str | None = None) -> Incident:
    """Close an incident by hand — for outages the probes cannot see recovering."""
    if not incident.is_open:
        return incident

    incident.resolved_at = datetime.now(timezone.utc)
    incident.status = IncidentStatus.RESOLVED
    if note:
        incident.root_cause = f"{incident.root_cause or ''}\n[manually resolved] {note}".strip()

    monitor = db.session.get(Monitor, incident.monitor_id)
    if monitor is not None:
        monitor.consecutive_failures = 0
        monitor.consecutive_degraded = 0
    return incident


def incident_summary(monitor_id, since) -> dict:
    """Incident counts and accumulated downtime for a monitor over a window."""
    rows = (
        db.session.query(
            Incident.status,
            sa.func.count(Incident.id),
            sa.func.sum(
                sa.func.extract(
                    "epoch",
                    sa.func.coalesce(Incident.resolved_at, sa.func.now()) - Incident.started_at,
                )
            ),
        )
        .filter(Incident.monitor_id == monitor_id, Incident.started_at >= since)
        .group_by(Incident.status)
        .all()
    )
    return {
        (status.value if hasattr(status, "value") else str(status)): {
            "count": count,
            "total_seconds": float(seconds or 0),
        }
        for status, count, seconds in rows
    }
