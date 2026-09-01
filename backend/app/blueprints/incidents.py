"""Incident board: what is broken now, and what broke recently."""
import uuid
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from flask import Blueprint, jsonify, request

from app.extensions import db
from app.models import Incident, IncidentStatus, Monitor, UserRole
from app.services import incidents as incident_service
from app.utils.errors import APIError
from app.utils.tenancy import (
    auth_required,
    current_org_id,
    get_tenant_object_or_404,
    roles_required,
    tenant_query,
)
from app.utils.validators import clean_int

incidents_bp = Blueprint("incidents", __name__)


def _parse_uuid(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise APIError("Malformed incident id", 400) from None


@incidents_bp.get("")
@auth_required
def list_incidents():
    """Org-wide incident feed. Defaults to open incidents first, newest first."""
    page = clean_int(request.args.get("page"), field="page", minimum=1, maximum=10_000, default=1)
    per_page = clean_int(
        request.args.get("per_page"), field="per_page", minimum=1, maximum=100, default=25
    )
    days = clean_int(request.args.get("days"), field="days", minimum=1, maximum=365, default=30)

    query = tenant_query(Incident).filter(
        Incident.started_at >= datetime.now(timezone.utc) - timedelta(days=days)
    )

    state = (request.args.get("state") or "").lower()
    if state == "open":
        query = query.filter(Incident.resolved_at.is_(None))
    elif state == "resolved":
        query = query.filter(Incident.resolved_at.isnot(None))
    elif state:
        raise APIError("Unknown state filter", 422, {"allowed": ["open", "resolved"]})

    if monitor_id := request.args.get("monitor_id"):
        query = query.filter(Incident.monitor_id == _parse_uuid(monitor_id))

    total = query.count()
    rows = (
        query.order_by(
            # Open incidents first — an operator opening this page cares about
            # what is broken now, not what was broken last week.
            sa.nullsfirst(Incident.resolved_at.asc()),
            Incident.started_at.desc(),
        )
        .limit(per_page)
        .offset((page - 1) * per_page)
        .all()
    )

    monitors = {
        m.id: m.name
        for m in db.session.query(Monitor.id, Monitor.name).filter(
            Monitor.id.in_([r.monitor_id for r in rows])
        )
    } if rows else {}

    return jsonify(
        {
            "incidents": [
                {**r.to_dict(), "monitor_name": monitors.get(r.monitor_id)} for r in rows
            ],
            "pagination": {"page": page, "per_page": per_page, "total": total},
        }
    )


@incidents_bp.get("/summary")
@auth_required
def summary():
    """Counts by status for the org — the dashboard's header tiles."""
    days = clean_int(request.args.get("days"), field="days", minimum=1, maximum=365, default=30)
    since = datetime.now(timezone.utc) - timedelta(days=days)

    rows = (
        db.session.query(Incident.status, sa.func.count(Incident.id))
        .filter(Incident.org_id == current_org_id(), Incident.started_at >= since)
        .group_by(Incident.status)
        .all()
    )
    counts = {status.value: count for status, count in rows}

    open_count = (
        tenant_query(Incident).filter(Incident.resolved_at.is_(None)).count()
    )

    return jsonify(
        {
            "window_days": days,
            "open": open_count,
            "by_status": {s.value: counts.get(s.value, 0) for s in IncidentStatus},
        }
    )


@incidents_bp.get("/<incident_id>")
@auth_required
def get_incident(incident_id: str):
    incident = get_tenant_object_or_404(Incident, _parse_uuid(incident_id))
    monitor = db.session.get(Monitor, incident.monitor_id)
    return jsonify(
        {"incident": {**incident.to_dict(), "monitor": monitor.to_dict() if monitor else None}}
    )


@incidents_bp.post("/<incident_id>/resolve")
@roles_required(UserRole.ADMIN, UserRole.ENGINEER)
def resolve_incident(incident_id: str):
    """Close an incident by hand, for outages the probes cannot see recovering."""
    incident = get_tenant_object_or_404(Incident, _parse_uuid(incident_id))
    if not incident.is_open:
        raise APIError("Incident is already resolved", 409)

    payload = request.get_json(silent=True) or {}
    incident_service.resolve_manually(incident, payload.get("note"))
    db.session.commit()

    from app.tasks.alerts import notify_incident

    notify_incident.delay(str(incident.id), "resolved")
    return jsonify({"incident": incident.to_dict()})
