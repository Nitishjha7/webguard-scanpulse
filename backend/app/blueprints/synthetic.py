"""Synthetic check management and run history."""
import os
import uuid
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from flask import Blueprint, current_app, jsonify, request, send_from_directory
from sqlalchemy.exc import IntegrityError

from app.engines.synthetic import STEP_SCHEMA, ValidationError, validate_steps
from app.extensions import db
from app.models import Monitor, RunStatus, SyntheticCheck, SyntheticRun, UserRole, redact_steps
from app.utils.errors import APIError
from app.utils.tenancy import (
    auth_required,
    current_org_id,
    get_tenant_object_or_404,
    roles_required,
    tenant_query,
)
from app.utils.validators import clean_int, require_fields

synthetic_bp = Blueprint("synthetic", __name__)

MAX_CHECKS_PER_ORG = 50
MAX_INTERVAL_SECONDS = 86_400
MAX_TIMEOUT_SECONDS = 300


def _parse_uuid(raw: str, what: str = "check") -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise APIError(f"Malformed {what} id", 400) from None


def _clean_steps(raw):
    try:
        return validate_steps(raw)
    except ValidationError as exc:
        raise APIError(str(exc), 422, {"allowed_actions": sorted(STEP_SCHEMA)}) from None


def _merge_secrets(new_steps: list, old_steps: list | None) -> list:
    """Carry stored secrets through an update that echoed back the mask.

    GET redacts secret values, so a client that reads a check, edits the name
    and PUTs it back would otherwise overwrite the real password with
    ``********``. Where the submitted value is exactly the mask and the same
    step position held a secret before, keep the stored one.
    """
    from app.models.synthetic import REDACTED

    old_steps = old_steps or []
    merged = []
    for index, step in enumerate(new_steps):
        if (
            step.get("value") == REDACTED
            and index < len(old_steps)
            and old_steps[index].get("secret")
            and old_steps[index].get("action") == step.get("action")
        ):
            step = {**step, "value": old_steps[index]["value"]}
        merged.append(step)
    return merged


@synthetic_bp.get("")
@auth_required
def list_checks():
    rows = tenant_query(SyntheticCheck).order_by(SyntheticCheck.created_at.desc()).all()

    # Latest run per check, so the list can show health without an N+1.
    latest = {}
    if rows:
        newest = (
            sa.select(
                SyntheticRun.check_id,
                sa.func.max(SyntheticRun.started_at).label("started_at"),
            )
            .where(SyntheticRun.check_id.in_([r.id for r in rows]))
            .group_by(SyntheticRun.check_id)
            .subquery()
        )
        for run in (
            db.session.query(SyntheticRun)
            .join(
                newest,
                sa.and_(
                    SyntheticRun.check_id == newest.c.check_id,
                    SyntheticRun.started_at == newest.c.started_at,
                ),
            )
            .all()
        ):
            latest[run.check_id] = run.to_dict()

    return jsonify(
        {
            "checks": [
                {**c.to_dict(include_steps=False), "latest_run": latest.get(c.id)} for c in rows
            ],
            "available_actions": sorted(STEP_SCHEMA),
        }
    )


@synthetic_bp.post("")
@roles_required(UserRole.ADMIN, UserRole.ENGINEER)
def create_check():
    payload = require_fields(request.get_json(silent=True), "name", "steps")

    if tenant_query(SyntheticCheck).count() >= MAX_CHECKS_PER_ORG:
        raise APIError(
            "Synthetic check quota reached", 409, {"limit": MAX_CHECKS_PER_ORG}
        )

    monitor_id = payload.get("monitor_id")
    if monitor_id:
        # Resolved through the tenant query so a check cannot be attached to
        # another organization's monitor.
        get_tenant_object_or_404(Monitor, _parse_uuid(monitor_id, "monitor"))

    check = SyntheticCheck(
        org_id=current_org_id(),
        monitor_id=uuid.UUID(monitor_id) if monitor_id else None,
        name=str(payload["name"]).strip(),
        description=(payload.get("description") or None),
        steps=_clean_steps(payload["steps"]),
        interval_seconds=clean_int(
            payload.get("interval_seconds"),
            field="interval_seconds",
            minimum=60,
            maximum=MAX_INTERVAL_SECONDS,
            default=900,
        ),
        timeout_seconds=clean_int(
            payload.get("timeout_seconds"),
            field="timeout_seconds",
            minimum=5,
            maximum=MAX_TIMEOUT_SECONDS,
            default=60,
        ),
        viewport_width=clean_int(
            payload.get("viewport_width"), field="viewport_width", minimum=320, maximum=3840, default=1280
        ),
        viewport_height=clean_int(
            payload.get("viewport_height"), field="viewport_height", minimum=240, maximum=2160, default=720
        ),
        failure_threshold=clean_int(
            payload.get("failure_threshold"), field="failure_threshold", minimum=1, maximum=10, default=2
        ),
        is_active=bool(payload.get("is_active", True)),
    )
    db.session.add(check)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        raise APIError("A check with this name already exists", 409) from None

    return jsonify({"check": check.to_dict()}), 201


@synthetic_bp.get("/<check_id>")
@auth_required
def get_check(check_id: str):
    check = get_tenant_object_or_404(SyntheticCheck, _parse_uuid(check_id))
    return jsonify({"check": check.to_dict()})


@synthetic_bp.patch("/<check_id>")
@roles_required(UserRole.ADMIN, UserRole.ENGINEER)
def update_check(check_id: str):
    check = get_tenant_object_or_404(SyntheticCheck, _parse_uuid(check_id))
    payload = request.get_json(silent=True) or {}

    if "name" in payload:
        check.name = str(payload["name"]).strip()
    if "description" in payload:
        check.description = payload["description"] or None
    if "steps" in payload:
        check.steps = _merge_secrets(_clean_steps(payload["steps"]), check.steps)
    if "interval_seconds" in payload:
        check.interval_seconds = clean_int(
            payload["interval_seconds"],
            field="interval_seconds",
            minimum=60,
            maximum=MAX_INTERVAL_SECONDS,
            default=check.interval_seconds,
        )
    if "timeout_seconds" in payload:
        check.timeout_seconds = clean_int(
            payload["timeout_seconds"],
            field="timeout_seconds",
            minimum=5,
            maximum=MAX_TIMEOUT_SECONDS,
            default=check.timeout_seconds,
        )
    if "is_active" in payload:
        check.is_active = bool(payload["is_active"])
    if "failure_threshold" in payload:
        check.failure_threshold = clean_int(
            payload["failure_threshold"],
            field="failure_threshold",
            minimum=1,
            maximum=10,
            default=check.failure_threshold,
        )

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        raise APIError("A check with this name already exists", 409) from None

    return jsonify({"check": check.to_dict()})


@synthetic_bp.delete("/<check_id>")
@roles_required(UserRole.ADMIN)
def delete_check(check_id: str):
    check = get_tenant_object_or_404(SyntheticCheck, _parse_uuid(check_id))
    db.session.delete(check)
    db.session.commit()
    return "", 204


@synthetic_bp.post("/<check_id>/run")
@roles_required(UserRole.ADMIN, UserRole.ENGINEER)
def trigger_run(check_id: str):
    """Queue an immediate run instead of waiting for the next interval."""
    check = get_tenant_object_or_404(SyntheticCheck, _parse_uuid(check_id))

    from app.tasks.synthetic import run_synthetic_check

    async_result = run_synthetic_check.delay(str(check.id))
    return jsonify({"queued": True, "task_id": async_result.id}), 202


@synthetic_bp.get("/<check_id>/runs")
@auth_required
def list_runs(check_id: str):
    check = get_tenant_object_or_404(SyntheticCheck, _parse_uuid(check_id))
    days = clean_int(request.args.get("days"), field="days", minimum=1, maximum=90, default=7)
    limit = clean_int(request.args.get("limit"), field="limit", minimum=1, maximum=200, default=50)
    since = datetime.now(timezone.utc) - timedelta(days=days)

    base = db.session.query(SyntheticRun).filter(
        SyntheticRun.check_id == check.id, SyntheticRun.started_at >= since
    )
    rows = base.order_by(SyntheticRun.started_at.desc()).limit(limit).all()

    total, passed, avg_duration = (
        db.session.query(
            sa.func.count(SyntheticRun.id),
            sa.func.count(sa.case((SyntheticRun.status == RunStatus.PASSED, 1))),
            sa.func.avg(SyntheticRun.duration_ms),
        )
        .filter(SyntheticRun.check_id == check.id, SyntheticRun.started_at >= since)
        .one()
    )

    return jsonify(
        {
            "check_id": str(check.id),
            "window_days": days,
            "summary": {
                "runs": total,
                "passed": passed,
                "failed": total - passed,
                "success_rate": round(passed / total * 100, 2) if total else None,
                "avg_duration_ms": round(float(avg_duration), 2) if avg_duration else None,
            },
            "runs": [r.to_dict() for r in rows],
        }
    )


@synthetic_bp.get("/runs/<run_id>")
@auth_required
def get_run(run_id: str):
    run = get_tenant_object_or_404(SyntheticRun, _parse_uuid(run_id, "run"))
    check = db.session.get(SyntheticCheck, run.check_id)
    return jsonify(
        {
            "run": run.to_dict(),
            "check": {"id": str(check.id), "name": check.name} if check else None,
            "steps": redact_steps(check.steps) if check else None,
        }
    )


@synthetic_bp.get("/runs/<run_id>/screenshot")
@auth_required
def get_screenshot(run_id: str):
    """Serve the failure screenshot for a run in the caller's own organization."""
    run = get_tenant_object_or_404(SyntheticRun, _parse_uuid(run_id, "run"))
    if not run.screenshot:
        raise APIError("This run has no screenshot", 404)

    directory = current_app.config["ARTIFACTS_DIR"]
    # The filename was generated by us, never taken from user input, but check
    # anyway: serving an arbitrary path from a shared volume is a file read.
    if os.path.basename(run.screenshot) != run.screenshot:
        raise APIError("Invalid screenshot reference", 400)
    if not os.path.exists(os.path.join(directory, run.screenshot)):
        raise APIError("Screenshot is no longer available", 404)

    return send_from_directory(directory, run.screenshot, mimetype="image/png")
