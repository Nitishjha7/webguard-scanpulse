"""Blueprint registry. One place that knows every URL prefix in the API."""
from flask import Flask

from app.blueprints.auth import auth_bp
from app.blueprints.channels import channels_bp
from app.blueprints.health import health_bp
from app.blueprints.incidents import incidents_bp
from app.blueprints.monitors import monitors_bp

API_PREFIX = "/api/v1"


def register_blueprints(app: Flask) -> None:
    app.register_blueprint(health_bp, url_prefix="/health")
    app.register_blueprint(auth_bp, url_prefix=f"{API_PREFIX}/auth")
    app.register_blueprint(monitors_bp, url_prefix=f"{API_PREFIX}/monitors")
    app.register_blueprint(incidents_bp, url_prefix=f"{API_PREFIX}/incidents")
    app.register_blueprint(channels_bp, url_prefix=f"{API_PREFIX}/channels")
