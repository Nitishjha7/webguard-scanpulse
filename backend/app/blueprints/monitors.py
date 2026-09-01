"""Monitor CRUD. Every read and write is scoped to the caller's organization."""
import uuid
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from flask import Blueprint, current_app, jsonify, request

from app.extensions import db
from app.models import Monitor, PingLog, SecurityAudit, SslScan, UserRole
from app.utils.errors import APIError
from app.utils.tenancy import (
    auth_required,
    current_org_id,
    get_tenant_object_or_404,
    roles_required,
    tenant_query,
)
from app.utils.validators import clean_int, clean_monitor_url, require_fields

monitors_bp = Blueprint("monitors", __name__)

MAX_INTERVAL_SECONDS = 86_400
MAX_TIMEOUT_SECONDS = 120


def _parse_uuid(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise APIError("Malformed monitor id", 400) from None


@monitors_bp.get("")
@auth_required
def list_monitors():
    page = clean_int(request.args.get("page"), field="page", minimum=1, maximum=10_000, default=1)
    per_page = clean_int(
        request.args.get("per_page"), field="per_page", minimum=1, maximum=100, default=25
    )

    query = tenant_query(Monitor)
    if (active := request.args.get("is_active")) is not None:
        query = query.filter(Monitor.is_active.is_(active.lower() in {"1", "true", "yes"}))

    total = query.count()
    rows = (
        query.order_by(Monitor.created_at.desc())
        .limit(per_page)
        .offset((page - 1) * per_page)
        .all()
    )
    return jsonify(
        {
            "monitors": [m.to_dict() for m in rows],
            "pagination": {"page": page, "per_page": per_page, "total": total},
        }
    )


@monitors_bp.post("")
@roles_required(UserRole.ADMIN, UserRole.ENGINEER)
def create_monitor():
    payload = require_fields(request.get_json(silent=True), "name", "url")
    url = clean_monitor_url(payload["url"])

    quota = current_app.config["MAX_MONITORS_PER_ORG"]
    if tenant_query(Monitor).count() >= quota:
        raise APIError("Monitor quota reached for this organization", 409, {"limit": quota})

    if tenant_query(Monitor).filter(Monitor.url == url).first():
        raise APIError("This URL is already monitored by your organization", 409)

    monitor = Monitor(
        org_id=current_org_id(),
        name=payload["name"].strip(),
        url=url,
        interval_seconds=clean_int(
            payload.get("interval_seconds"),
            field="interval_seconds",
            minimum=current_app.config["MIN_INTERVAL_SECONDS"],
            maximum=MAX_INTERVAL_SECONDS,
            default=300,
        ),
        timeout_seconds=clean_int(
            payload.get("timeout_seconds"),
            field="timeout_seconds",
            minimum=1,
            maximum=MAX_TIMEOUT_SECONDS,
            default=10,
        ),
        is_active=bool(payload.get("is_active", True)),
    )
    db.session.add(monitor)
    db.session.commit()
    return jsonify({"monitor": monitor.to_dict()}), 201


@monitors_bp.get("/<monitor_id>")
@auth_required
def get_monitor(monitor_id: str):
    monitor = get_tenant_object_or_404(Monitor, _parse_uuid(monitor_id))
    return jsonify({"monitor": monitor.to_dict()})


@monitors_bp.patch("/<monitor_id>")
@roles_required(UserRole.ADMIN, UserRole.ENGINEER)
def update_monitor(monitor_id: str):
    monitor = get_tenant_object_or_404(Monitor, _parse_uuid(monitor_id))
    payload = request.get_json(silent=True) or {}

    if "name" in payload:
        monitor.name = str(payload["name"]).strip()
    if "url" in payload:
        monitor.url = clean_monitor_url(payload["url"])
    if "interval_seconds" in payload:
        monitor.interval_seconds = clean_int(
            payload["interval_seconds"],
            field="interval_seconds",
            minimum=current_app.config["MIN_INTERVAL_SECONDS"],
            maximum=MAX_INTERVAL_SECONDS,
            default=monitor.interval_seconds,
        )
    if "timeout_seconds" in payload:
        monitor.timeout_seconds = clean_int(
            payload["timeout_seconds"],
            field="timeout_seconds",
            minimum=1,
            maximum=MAX_TIMEOUT_SECONDS,
            default=monitor.timeout_seconds,
        )
    if "is_active" in payload:
        monitor.is_active = bool(payload["is_active"])

    db.session.commit()
    return jsonify({"monitor": monitor.to_dict()})


@monitors_bp.delete("/<monitor_id>")
@roles_required(UserRole.ADMIN)
def delete_monitor(monitor_id: str):
    monitor = get_tenant_object_or_404(Monitor, _parse_uuid(monitor_id))
    db.session.delete(monitor)
    db.session.commit()
    return "", 204


# --- Probe results (Phase 2) ----------------------------------------------


@monitors_bp.get("/<monitor_id>/pings")
@auth_required
def list_pings(monitor_id: str):
    """Recent uptime probes, newest first, plus a rolled-up summary."""
    monitor = get_tenant_object_or_404(Monitor, _parse_uuid(monitor_id))
    hours = clean_int(request.args.get("hours"), field="hours", minimum=1, maximum=720, default=24)
    limit = clean_int(request.args.get("limit"), field="limit", minimum=1, maximum=1000, default=100)

    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    base = db.session.query(PingLog).filter(
        PingLog.monitor_id == monitor.id, PingLog.checked_at >= since
    )

    rows = base.order_by(PingLog.checked_at.desc()).limit(limit).all()

    total, up_count, avg_latency = (
        db.session.query(
            sa.func.count(PingLog.id),
            sa.func.count(sa.case((PingLog.is_up.is_(True), 1))),
            sa.func.avg(PingLog.latency_ms),
        )
        .filter(PingLog.monitor_id == monitor.id, PingLog.checked_at >= since)
        .one()
    )

    return jsonify(
        {
            "monitor_id": str(monitor.id),
            "window_hours": hours,
            "summary": {
                "checks": total,
                "up": up_count,
                "down": total - up_count,
                "uptime_percent": round(up_count / total * 100, 3) if total else None,
                "avg_latency_ms": round(float(avg_latency), 2) if avg_latency is not None else None,
            },
            "pings": [p.to_dict() for p in rows],
        }
    )


@monitors_bp.get("/<monitor_id>/ssl")
@auth_required
def latest_ssl(monitor_id: str):
    monitor = get_tenant_object_or_404(Monitor, _parse_uuid(monitor_id))
    scan = (
        db.session.query(SslScan)
        .filter(SslScan.monitor_id == monitor.id)
        .order_by(SslScan.created_at.desc())
        .first()
    )
    if scan is None:
        raise APIError("No SSL scan recorded yet for this monitor", 404)
    return jsonify({"ssl_scan": scan.to_dict()})


@monitors_bp.get("/<monitor_id>/security")
@auth_required
def latest_security_audit(monitor_id: str):
    monitor = get_tenant_object_or_404(Monitor, _parse_uuid(monitor_id))
    audit = (
        db.session.query(SecurityAudit)
        .filter(SecurityAudit.monitor_id == monitor.id)
        .order_by(SecurityAudit.created_at.desc())
        .first()
    )
    if audit is None:
        raise APIError("No security audit recorded yet for this monitor", 404)
    return jsonify({"security_audit": audit.to_dict()})


@monitors_bp.post("/<monitor_id>/scan")
@roles_required(UserRole.ADMIN, UserRole.ENGINEER)
def trigger_scan(monitor_id: str):
    """Queue an immediate full scan instead of waiting for the next beat tick."""
    monitor = get_tenant_object_or_404(Monitor, _parse_uuid(monitor_id))

    from app.tasks.probes import probe_monitor, scan_monitor_full

    kind = (request.args.get("kind") or "full").lower()
    if kind == "ping":
        async_result = probe_monitor.delay(str(monitor.id))
    elif kind == "full":
        async_result = scan_monitor_full.delay(str(monitor.id))
    else:
        raise APIError("Unknown scan kind", 422, {"allowed": ["ping", "full"]})

    return jsonify({"queued": True, "kind": kind, "task_id": async_result.id}), 202
