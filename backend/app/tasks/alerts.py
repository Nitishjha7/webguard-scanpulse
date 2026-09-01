"""Alert delivery tasks.

Notification runs in its own task, off the probe's critical path. A slow Slack
API must never delay the next uptime check, and a failed delivery must be
retryable without re-running the probe.
"""
import logging

import redis as redis_lib
from celery import shared_task
from flask import current_app

from app.extensions import db
from app.models import AlertEvent, Incident, Monitor, SslScan
from app.notifications import broadcast, incident_message, ssl_message

logger = logging.getLogger(__name__)

#: How long a delivered-alert marker lives. Long enough to swallow a retry
#: storm, short enough that a genuinely recurring incident alerts again.
DEDUPE_TTL_SECONDS = 3600


def _redis():
    return redis_lib.from_url(current_app.config["REDIS_URL"])


def _claim(key: str, ttl: int = DEDUPE_TTL_SECONDS) -> bool:
    """Best-effort once-only guard.

    ``task_acks_late`` means a worker killed mid-delivery will re-run the task,
    so without this a restart can page everyone twice. If Redis is unreachable
    we send anyway: a duplicate alert beats a silent outage.
    """
    try:
        return bool(_redis().set(key, "1", nx=True, ex=ttl))
    except redis_lib.RedisError as exc:
        logger.warning("dedupe check failed (%s) — sending anyway", exc)
        return True


def _record(incident: Incident, results: list[dict]) -> None:
    """Append delivery receipts to the incident's own audit trail."""
    incident.notifications = list(incident.notifications or []) + results
    db.session.commit()


@shared_task(name="webguard.notify_incident", max_retries=3, default_retry_delay=60)
def notify_incident(incident_id: str, transition: str) -> dict:
    """Fan an incident transition out to the tenant's channels."""
    import uuid

    incident = db.session.get(Incident, uuid.UUID(str(incident_id)))
    if incident is None:
        return {"skipped": "incident not found"}

    monitor = db.session.get(Monitor, incident.monitor_id)
    if monitor is None:
        return {"skipped": "monitor not found"}

    event = (
        AlertEvent.INCIDENT_RESOLVED
        if transition == "resolved"
        else AlertEvent.INCIDENT_OPENED
    )

    if not _claim(f"alert:incident:{incident_id}:{transition}"):
        logger.info("incident %s %s already notified — skipping", incident_id, transition)
        return {"skipped": "already notified"}

    results = broadcast(incident.org_id, event, incident_message(incident, monitor, event))
    _record(incident, results)
    return {"event": event.value, "delivered": sum(1 for r in results if r["ok"]), "results": results}


@shared_task(name="webguard.notify_ssl", max_retries=3, default_retry_delay=60)
def notify_ssl(scan_id: str, event_name: str) -> dict:
    """Certificate expiry countdown / invalid-certificate alert."""
    import uuid

    scan = db.session.get(SslScan, uuid.UUID(str(scan_id)))
    if scan is None:
        return {"skipped": "scan not found"}

    monitor = db.session.get(Monitor, scan.monitor_id)
    if monitor is None:
        return {"skipped": "monitor not found"}

    event = AlertEvent(event_name)

    # Keyed on the monitor and the days-left mark, not the scan id: re-scanning
    # the same certificate on the same day must not alert twice.
    if not _claim(f"alert:ssl:{monitor.id}:{event.value}:{scan.days_left}", ttl=86_400):
        return {"skipped": "already notified"}

    results = broadcast(monitor.org_id, event, ssl_message(monitor, scan, event))
    db.session.commit()
    return {"event": event.value, "delivered": sum(1 for r in results if r["ok"]), "results": results}


@shared_task(name="webguard.notify_test", max_retries=0)
def notify_test(channel_id: str) -> dict:
    """Send a synthetic alert so a user can verify a channel actually works."""
    import uuid

    from app.models import NotificationChannel
    from app.notifications.dispatcher import deliver

    channel = db.session.get(NotificationChannel, uuid.UUID(str(channel_id)))
    if channel is None:
        return {"skipped": "channel not found"}

    message = {
        "event": "test",
        "severity": "RESOLVED",
        "title": "WebGuard test alert",
        "summary": f"If you can read this, '{channel.name}' is wired up correctly.",
        "url": "https://webguard.local",
        "fields": [("Channel", channel.name), ("Type", channel.type.value)],
    }
    ok, detail = deliver(channel, message)
    db.session.commit()
    return {"ok": ok, "detail": detail}
