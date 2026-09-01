"""Liveness / readiness probes for Docker healthchecks and load balancers."""
import sqlalchemy as sa
from flask import Blueprint, jsonify

from app.extensions import db

health_bp = Blueprint("health", __name__)


@health_bp.get("/live")
def liveness():
    """Process is up. Never touches the database."""
    return jsonify({"status": "ok"})


@health_bp.get("/ready")
def readiness():
    """Up *and* able to reach Postgres."""
    try:
        db.session.execute(sa.text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - reported, not swallowed
        return jsonify({"status": "degraded", "database": str(exc)}), 503
    return jsonify({"status": "ok", "database": "ok"})
