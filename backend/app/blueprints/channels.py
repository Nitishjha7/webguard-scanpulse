"""Notification channel management."""
import uuid

from flask import Blueprint, jsonify, request
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import AlertEvent, ChannelType, NotificationChannel, UserRole
from app.utils.errors import APIError
from app.utils.tenancy import (
    auth_required,
    current_org_id,
    get_tenant_object_or_404,
    roles_required,
    tenant_query,
)
from app.utils.validators import clean_email, require_fields
from app.utils.ssrf import SSRFError, assert_safe_url

channels_bp = Blueprint("channels", __name__)


def _parse_uuid(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise APIError("Malformed channel id", 400) from None


def _clean_target(channel_type: ChannelType, raw: str) -> str:
    """Validate a destination for its channel type.

    Webhook URLs are user-supplied and get fetched by a worker, so they are
    SSRF-checked here at creation time as well as at send time — better to
    reject a pointer at localhost when the user can still see the error.
    """
    target = raw.strip()
    if channel_type is ChannelType.EMAIL:
        return clean_email(target)

    try:
        assert_safe_url(target)
    except SSRFError as exc:
        raise APIError(f"Invalid webhook URL: {exc}", 422) from None

    if not target.startswith("https://"):
        raise APIError("Webhook URLs must use https", 422)
    return target


def _clean_events(raw) -> list[str] | None:
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise APIError("events must be a list", 422)
    allowed = {e.value for e in AlertEvent}
    unknown = [e for e in raw if e not in allowed]
    if unknown:
        raise APIError("Unknown event names", 422, {"unknown": unknown, "allowed": sorted(allowed)})
    return raw or None


@channels_bp.get("")
@auth_required
def list_channels():
    rows = tenant_query(NotificationChannel).order_by(NotificationChannel.created_at.desc()).all()
    return jsonify(
        {
            "channels": [c.to_dict() for c in rows],
            "available_events": [e.value for e in AlertEvent],
            "available_types": [t.value for t in ChannelType],
        }
    )


@channels_bp.post("")
@roles_required(UserRole.ADMIN)
def create_channel():
    payload = require_fields(request.get_json(silent=True), "name", "type", "target")

    try:
        channel_type = ChannelType(str(payload["type"]).lower())
    except ValueError:
        raise APIError(
            "Unknown channel type", 422, {"allowed": [t.value for t in ChannelType]}
        ) from None

    channel = NotificationChannel(
        org_id=current_org_id(),
        name=str(payload["name"]).strip(),
        type=channel_type,
        target=_clean_target(channel_type, payload["target"]),
        events=_clean_events(payload.get("events")),
        is_active=bool(payload.get("is_active", True)),
    )
    db.session.add(channel)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        raise APIError("This destination is already configured", 409) from None

    return jsonify({"channel": channel.to_dict()}), 201


@channels_bp.patch("/<channel_id>")
@roles_required(UserRole.ADMIN)
def update_channel(channel_id: str):
    channel = get_tenant_object_or_404(NotificationChannel, _parse_uuid(channel_id))
    payload = request.get_json(silent=True) or {}

    if "name" in payload:
        channel.name = str(payload["name"]).strip()
    if "target" in payload:
        channel.target = _clean_target(channel.type, payload["target"])
    if "events" in payload:
        channel.events = _clean_events(payload["events"])
    if "is_active" in payload:
        channel.is_active = bool(payload["is_active"])
        if channel.is_active:
            # Re-enabling clears the strike count that disabled it.
            channel.failure_count = 0
            channel.last_error = None

    db.session.commit()
    return jsonify({"channel": channel.to_dict()})


@channels_bp.delete("/<channel_id>")
@roles_required(UserRole.ADMIN)
def delete_channel(channel_id: str):
    channel = get_tenant_object_or_404(NotificationChannel, _parse_uuid(channel_id))
    db.session.delete(channel)
    db.session.commit()
    return "", 204


@channels_bp.post("/<channel_id>/test")
@roles_required(UserRole.ADMIN, UserRole.ENGINEER)
def test_channel(channel_id: str):
    """Send a synthetic alert so the user can confirm the wiring works.

    Runs inline rather than queued: the caller is waiting to find out whether
    their webhook URL is correct, and a task id tells them nothing.
    """
    channel = get_tenant_object_or_404(NotificationChannel, _parse_uuid(channel_id))

    from app.notifications.dispatcher import deliver

    message = {
        "event": "test",
        "severity": "RESOLVED",
        "title": "WebGuard test alert",
        "summary": f"If you can read this, '{channel.name}' is wired up correctly.",
        "url": "https://example.com",
        "fields": [("Channel", channel.name), ("Type", channel.type.value)],
    }
    ok, detail = deliver(channel, message)
    db.session.commit()

    return jsonify({"ok": ok, "detail": detail}), (200 if ok else 502)
