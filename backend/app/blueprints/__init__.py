"""Blueprint registry. One place that knows every URL prefix in the API."""
from flask import Flask

from app.blueprints.auth import auth_bp
from app.blueprints.health import health_bp
from app.blueprints.monitors import monitors_bp

API_PREFIX = "/api/v1"


def register_blueprints(app: Flask) -> None:
    app.register_blueprint(health_bp, url_prefix="/health")
    app.register_blueprint(auth_bp, url_prefix=f"{API_PREFIX}/auth")
    app.register_blueprint(monitors_bp, url_prefix=f"{API_PREFIX}/monitors")
