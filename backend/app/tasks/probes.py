"""Per-monitor probe tasks.

Each task owns one monitor and one probe type. They are idempotent — running
one twice writes two result rows and nothing else — which is what lets the
worker use ``task_acks_late`` and survive being killed mid-probe.
"""
import logging
from datetime import datetime, timezone
from urllib.parse import urlparse

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from flask import current_app

from app.engines import (
    audit_dns_posture,
    audit_security_headers,
    inspect_ssl_certificate,
    probe_http,
)
from app.extensions import db
from app.models import AlertEvent, Monitor, PingLog, SecurityAudit, SslScan
from app.services import incidents
from app.tasks.alerts import notify_incident, notify_ssl

logger = logging.getLogger(__name__)


def _load_monitor(monitor_id: str) -> Monitor | None:
    import uuid

    monitor = db.session.get(Monitor, uuid.UUID(str(monitor_id)))
    if monitor is None:
        logger.warning("Monitor %s no longer exists — skipping probe", monitor_id)
    return monitor


def _parse_dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def run_probe(monitor_id: str, region: str = "default") -> dict:
    """Single uptime + latency check, persisted to ``ping_logs``."""
    monitor = _load_monitor(monitor_id)
    if monitor is None:
        return {"skipped": "monitor not found"}

    try:
        result = probe_http(monitor.url, timeout=monitor.timeout_seconds, region=region)
    except SoftTimeLimitExceeded:
        result = {
            "region": region,
            "status_code": None,
            "latency_ms": None,
            "is_up": False,
            "error": "probe exceeded soft time limit",
        }

    db.session.add(
        PingLog(
            monitor_id=monitor.id,
            org_id=monitor.org_id,
            region=result["region"],
            status_code=result["status_code"],
            latency_ms=result["latency_ms"],
            is_up=result["is_up"],
            error=result["error"],
        )
    )
    # Stamped even on failure, so a permanently broken target does not get
    # re-dispatched on every single beat tick.
    monitor.last_checked_at = datetime.now(timezone.utc)

    # Fold this probe into the incident state machine inside the same
    # transaction as the ping row, so the two can never disagree.
    transition = incidents.evaluate(monitor, result)
    db.session.commit()

    # Notify only after the commit: an alert about an incident that got rolled
    # back is worse than one that arrives a moment late.
    if transition.get("transition") in {"opened", "escalated", "resolved"}:
        notify_incident.delay(transition["incident_id"], transition["transition"])

    result["incident"] = transition

    logger.info(
        "probe %s %s -> up=%s status=%s %sms",
        monitor.name,
        monitor.url,
        result["is_up"],
        result["status_code"],
        result["latency_ms"],
    )
    return result


def run_ssl_scan(monitor_id: str) -> dict:
    """TLS handshake + certificate decode, persisted to ``ssl_scans``."""
    monitor = _load_monitor(monitor_id)
    if monitor is None:
        return {"skipped": "monitor not found"}

    parsed = urlparse(monitor.url)
    if parsed.scheme != "https":
        return {"skipped": f"{monitor.url} is not https"}

    result = inspect_ssl_certificate(parsed.hostname, parsed.port or 443, timeout=monitor.timeout_seconds)

    previous = (
        db.session.query(SslScan)
        .filter(SslScan.monitor_id == monitor.id, SslScan.days_left.isnot(None))
        .order_by(SslScan.created_at.desc())
        .first()
    )

    scan = SslScan(
        monitor_id=monitor.id,
        org_id=monitor.org_id,
        issuer=result.get("issuer"),
        subject=result.get("subject"),
        valid_from=_parse_dt(result.get("valid_from")),
        valid_to=_parse_dt(result.get("valid_to")),
        days_left=result.get("days_left"),
        tls_version=result.get("tls_version"),
        cipher=result.get("cipher"),
        is_valid=bool(result.get("is_valid")),
        verify_error=result.get("verify_error"),
        error=result.get("error"),
        payload=result,
    )
    db.session.add(scan)
    db.session.commit()

    _raise_ssl_alerts(scan, previous)

    logger.info(
        "ssl %s -> valid=%s days_left=%s tls=%s",
        monitor.url,
        result.get("is_valid"),
        result.get("days_left"),
        result.get("tls_version"),
    )
    return result


def run_security_scan(monitor_id: str) -> dict:
    """Header audit + DNS posture in one row of ``security_audits``."""
    monitor = _load_monitor(monitor_id)
    if monitor is None:
        return {"skipped": "monitor not found"}

    headers = audit_security_headers(monitor.url, timeout=monitor.timeout_seconds)
    hostname = urlparse(monitor.url).hostname or ""
    dns_result = audit_dns_posture(_registrable_domain(hostname))

    audit = SecurityAudit(
        monitor_id=monitor.id,
        org_id=monitor.org_id,
        score=headers.get("score", 0),
        grade=headers.get("grade", "F"),
        dns_score=dns_result.get("score"),
        dns_grade=dns_result.get("grade"),
        headers_payload=headers,
        dns_payload=dns_result,
        error=headers.get("error") or dns_result.get("error"),
    )
    db.session.add(audit)
    monitor.last_scanned_at = datetime.now(timezone.utc)
    db.session.commit()

    logger.info(
        "security %s -> headers=%s dns=%s",
        monitor.url,
        headers.get("grade"),
        dns_result.get("grade"),
    )
    return {"headers": headers, "dns": dns_result}


def _raise_ssl_alerts(scan: SslScan, previous: SslScan | None) -> None:
    """Alert on an invalid certificate, or on crossing an expiry threshold.

    Threshold crossing is derived from the previous scan rather than stored
    state: a mark fires only when it sits between the last reading and this one,
    so passing 30 days does not then re-alert every day until 14.
    """
    if scan.error:
        return  # Could not reach the host at all; the uptime probe owns that.

    # verify_error means the chain itself failed — expired, self-signed, or
    # issued for a different host. That is an alert on its own, not a countdown.
    if scan.verify_error:
        notify_ssl.delay(str(scan.id), AlertEvent.SSL_INVALID.value)
        return

    days = scan.days_left
    if days is None:
        return

    thresholds = current_app.config["SSL_EXPIRY_THRESHOLDS"]
    # No prior reading means treat every mark above the current value as newly
    # crossed — a monitor added with a cert expiring in 5 days must alert now.
    ceiling = previous.days_left if previous is not None else max(thresholds, default=0) + 1

    crossed = [t for t in thresholds if days <= t < ceiling]
    if crossed:
        logger.warning(
            "ssl expiry threshold crossed for %s: %s days left (marks %s)",
            scan.monitor_id,
            days,
            crossed,
        )
        notify_ssl.delay(str(scan.id), AlertEvent.SSL_EXPIRING.value)


def _registrable_domain(hostname: str) -> str:
    """Strip a leading ``www.`` so SPF/DMARC are looked up on the apex.

    Deliberately naive — a full public-suffix list is overkill here, and
    ``www`` is the only prefix that reliably hides the apex in practice.
    """
    return hostname[4:] if hostname.lower().startswith("www.") else hostname


# --- Celery task wrappers -------------------------------------------------
#
# The work above lives in plain functions so it can be called directly (from a
# CLI, a test, or the composite task below) without going through Celery.


@shared_task(name="webguard.probe_monitor", max_retries=0)
def probe_monitor(monitor_id: str, region: str = "default") -> dict:
    return run_probe(monitor_id, region)


@shared_task(name="webguard.scan_monitor_ssl", max_retries=0)
def scan_monitor_ssl(monitor_id: str) -> dict:
    return run_ssl_scan(monitor_id)


@shared_task(name="webguard.scan_monitor_security", max_retries=0)
def scan_monitor_security(monitor_id: str) -> dict:
    return run_security_scan(monitor_id)


@shared_task(name="webguard.scan_monitor_full", max_retries=0)
def scan_monitor_full(monitor_id: str) -> dict:
    """Every check for one monitor. Backs the manual "scan now" endpoint."""
    return {
        "ping": run_probe(monitor_id),
        "ssl": run_ssl_scan(monitor_id),
        "security": run_security_scan(monitor_id),
    }
