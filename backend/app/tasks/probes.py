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

from app.engines import (
    audit_dns_posture,
    audit_security_headers,
    inspect_ssl_certificate,
    probe_http,
)
from app.extensions import db
from app.models import Monitor, PingLog, SecurityAudit, SslScan

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
    db.session.commit()

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

    db.session.add(
        SslScan(
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
    )
    db.session.commit()

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
