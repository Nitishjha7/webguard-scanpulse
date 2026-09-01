"""Monitor CRUD. Every read and write is scoped to the caller's organization."""
import uuid

from flask import Blueprint, current_app, jsonify, request

from app.extensions import db
from app.models import Monitor, UserRole
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
