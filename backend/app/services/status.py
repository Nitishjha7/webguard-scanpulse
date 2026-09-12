"""Assembles what a public status page shows.

Everything here runs for anonymous visitors, so the shape of the returned data
*is* the disclosure boundary: this module decides what leaves the building, and
the template only formats it. Nothing that is not built here can leak, even if
a template is later edited carelessly.
"""
import logging
import uuid
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa

from app.extensions import db
from app.models import (
    Incident,
    IncidentStatus,
    Monitor,
    PingLog,
    PingRollupDaily,
    StatusPage,
)

logger = logging.getLogger(__name__)

#: A monitor is "current" only if it has been probed recently; older than this
#: and we say so rather than showing a stale green tick.
FRESHNESS_WINDOW = timedelta(minutes=30)

OPERATIONAL = "operational"
DEGRADED = "degraded"
DOWN = "down"
UNKNOWN = "unknown"

#: Worst-first, so the overall banner takes the most severe component state.
SEVERITY_ORDER = {UNKNOWN: 0, OPERATIONAL: 1, DEGRADED: 2, DOWN: 3}


def find_page(slug: str | None = None, host: str | None = None) -> StatusPage | None:
    """Resolve a page by slug or by the Host header of a custom domain."""
    query = db.session.query(StatusPage).filter(StatusPage.is_published.is_(True))
    if slug:
        return query.filter(StatusPage.slug == slug).first()
    if host:
        # Strip any port before matching; browsers send host:port.
        hostname = host.split(":")[0].lower()
        return query.filter(StatusPage.custom_domain == hostname).first()
    return None


def _selected_monitors(page: StatusPage) -> list[Monitor]:
    """Monitors on this page, in the tenant's chosen order.

    Scoped to the page's own organization as well as its id list, so a stale or
    tampered id cannot pull in another tenant's monitor.
    """
    rows = (
        db.session.query(Monitor)
        .filter(Monitor.org_id == page.org_id, Monitor.is_active.is_(True))
        .all()
    )
    by_id = {str(m.id): m for m in rows}

    if not page.monitor_ids:
        return sorted(rows, key=lambda m: m.name.lower())

    ordered = [by_id[str(mid)] for mid in page.monitor_ids if str(mid) in by_id]
    return ordered


def _current_states(monitors: list[Monitor]) -> dict[uuid.UUID, str]:
    """Current state per monitor, from open incidents plus probe freshness."""
    if not monitors:
        return {}

    ids = [m.id for m in monitors]
    open_incidents = {
        incident.monitor_id: incident.status
        for incident in db.session.query(Incident)
        .filter(Incident.monitor_id.in_(ids), Incident.resolved_at.is_(None))
        .all()
    }

    now = datetime.now(timezone.utc)
    states = {}
    for monitor in monitors:
        status = open_incidents.get(monitor.id)
        if status is IncidentStatus.DOWN:
            states[monitor.id] = DOWN
        elif status is IncidentStatus.DEGRADED:
            states[monitor.id] = DEGRADED
        elif monitor.last_checked_at is None or now - monitor.last_checked_at > FRESHNESS_WINDOW:
            # No recent probe means we do not know, and saying "operational"
            # would be a claim we cannot support.
            states[monitor.id] = UNKNOWN
        else:
            states[monitor.id] = OPERATIONAL
    return states


def _daily_history(monitors: list[Monitor], days: int) -> dict[uuid.UUID, list[dict]]:
    """Per-day uptime bars, read from the daily rollup.

    Reading the rollup rather than raw probes is the whole point of Phase 5: a
    90-day page costs 90 rows per monitor instead of a quarter-million.
    """
    if not monitors:
        return {}

    since = (datetime.now(timezone.utc) - timedelta(days=days)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    rows = (
        db.session.query(PingRollupDaily)
        .filter(
            PingRollupDaily.monitor_id.in_([m.id for m in monitors]),
            PingRollupDaily.bucket >= since,
        )
        .order_by(PingRollupDaily.bucket.asc())
        .all()
    )

    history: dict[uuid.UUID, list[dict]] = {m.id: [] for m in monitors}
    for row in rows:
        history[row.monitor_id].append(
            {
                "date": row.bucket.date().isoformat(),
                "uptime_percent": row.uptime_percent,
                "checks": row.checks,
                "avg_latency_ms": row.avg_latency_ms,
            }
        )
    return history


def _today_so_far(monitors: list[Monitor]) -> dict[uuid.UUID, dict]:
    """Today's figures from raw probes.

    The daily rollup only covers completed days, so without this the page would
    show a gap for today — the day visitors care about most.
    """
    if not monitors:
        return {}

    midnight = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    rows = db.session.execute(
        sa.select(
            PingLog.monitor_id,
            sa.func.count(PingLog.id).label("checks"),
            sa.func.count(sa.case((PingLog.is_up.is_(True), 1))).label("up"),
            sa.func.avg(PingLog.latency_ms).label("avg_latency"),
        )
        .where(
            PingLog.monitor_id.in_([m.id for m in monitors]),
            PingLog.checked_at >= midnight,
        )
        .group_by(PingLog.monitor_id)
    )

    today = {}
    for row in rows:
        today[row.monitor_id] = {
            "date": midnight.date().isoformat(),
            "uptime_percent": round(row.up / row.checks * 100, 4) if row.checks else 0.0,
            "checks": row.checks,
            "avg_latency_ms": round(float(row.avg_latency), 2) if row.avg_latency else None,
        }
    return today


def _recent_incidents(page: StatusPage, monitors: list[Monitor], days: int = 30) -> list[dict]:
    """Incidents to publish.

    Root cause is not included. It is written by our probes and routinely holds
    internal detail — resolved IP addresses, DNS errors naming infrastructure —
    that has no place on a page the whole internet can read.
    """
    if not page.show_incidents or not monitors:
        return []

    names = {m.id: m.name for m in monitors}
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (
        db.session.query(Incident)
        .filter(Incident.monitor_id.in_(list(names)), Incident.started_at >= since)
        .order_by(Incident.started_at.desc())
        .limit(20)
        .all()
    )

    return [
        {
            "monitor": names[incident.monitor_id],
            "status": incident.status.value,
            "started_at": incident.started_at.isoformat(),
            "resolved_at": incident.resolved_at.isoformat() if incident.resolved_at else None,
            "duration_seconds": incident.duration_seconds,
            "is_open": incident.is_open,
        }
        for incident in rows
    ]


def _overall(states: dict[uuid.UUID, str]) -> str:
    if not states:
        return UNKNOWN
    return max(states.values(), key=lambda state: SEVERITY_ORDER[state])


def build_payload(page: StatusPage) -> dict:
    """Everything a public status page is allowed to show."""
    monitors = _selected_monitors(page)
    states = _current_states(monitors)
    history = _daily_history(monitors, page.history_days)
    today = _today_so_far(monitors)

    components = []
    for monitor in monitors:
        bars = list(history.get(monitor.id, []))
        if monitor.id in today:
            bars.append(today[monitor.id])

        measured = [b for b in bars if b["checks"]]
        overall_uptime = (
            round(sum(b["uptime_percent"] for b in measured) / len(measured), 3)
            if measured
            else None
        )

        component = {
            "name": monitor.name,
            "state": states.get(monitor.id, UNKNOWN),
            "uptime_percent": overall_uptime,
            "history": bars,
        }
        # Both of these are opt-in; a public page should say whether a service
        # is up, not where it lives or how fast it answered.
        if page.show_urls:
            component["url"] = monitor.url
        if page.show_latency and monitor.id in today:
            component["latency_ms"] = today[monitor.id]["avg_latency_ms"]
        components.append(component)

    return {
        "page": {
            "name": page.name,
            "slug": page.slug,
            "headline": page.headline,
            "description": page.description,
            "support_url": page.support_url,
            "history_days": page.history_days,
            "show_latency": page.show_latency,
        },
        "overall": _overall(states),
        "components": components,
        "incidents": _recent_incidents(page, monitors),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
