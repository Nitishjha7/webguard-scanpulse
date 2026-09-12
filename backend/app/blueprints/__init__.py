"""Blueprint registry. One place that knows every URL prefix in the API."""
from flask import Flask

from app.blueprints.auth import auth_bp
from app.blueprints.channels import channels_bp
from app.blueprints.health import health_bp
from app.blueprints.incidents import incidents_bp
from app.blueprints.monitors import monitors_bp
from app.blueprints.status import public_status_bp, status_admin_bp
from app.blueprints.synthetic import synthetic_bp

API_PREFIX = "/api/v1"


def register_blueprints(app: Flask) -> None:
    app.register_blueprint(health_bp, url_prefix="/health")
    app.register_blueprint(auth_bp, url_prefix=f"{API_PREFIX}/auth")
    app.register_blueprint(monitors_bp, url_prefix=f"{API_PREFIX}/monitors")
    app.register_blueprint(incidents_bp, url_prefix=f"{API_PREFIX}/incidents")
    app.register_blueprint(channels_bp, url_prefix=f"{API_PREFIX}/channels")
    app.register_blueprint(synthetic_bp, url_prefix=f"{API_PREFIX}/synthetic")
    app.register_blueprint(status_admin_bp, url_prefix=f"{API_PREFIX}/status-pages")
    # No prefix: public status pages live at /status/<slug>, outside the API.
    app.register_blueprint(public_status_bp)
