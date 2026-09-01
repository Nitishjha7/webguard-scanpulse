"""WebGuard (ScanPulse) — Flask application factory.

Everything the app needs is assembled here and nowhere else: config, extensions,
tenant context binding, blueprints, error handlers and CLI commands.
"""
import logging
import os

from flask import Flask, jsonify

from app.blueprints import register_blueprints
from app.celery_app import make_celery
from app.config import get_config
from app.extensions import cors, db, jwt, migrate
from app.utils.errors import register_error_handlers
from app.utils.tenancy import register_tenant_context

__version__ = "0.1.0"


def create_app(config_name: str | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_object(get_config(config_name))

    _configure_logging(app)
    _init_extensions(app)

    # Import models before Migrate reads metadata, so autogenerate sees every table.
    from app import models  # noqa: F401

    _init_celery(app)

    register_tenant_context(app)
    register_blueprints(app)
    register_error_handlers(app)
    _register_jwt_handlers()
    _register_cli(app)

    @app.get("/")
    def index():
        return jsonify(
            {
                "service": "webguard-scanpulse",
                "version": __version__,
                "docs": "/health/live, /api/v1/auth, /api/v1/monitors",
            }
        )

    return app


def _init_celery(app: Flask) -> None:
    """Build the Celery instance and import the task modules that register on it.

    The web process needs this too: it is how ``.delay()`` reaches the broker.
    """
    app.extensions["celery"] = make_celery(app)
    from app import tasks  # noqa: F401  - import registers every 


def _configure_logging(app: Flask) -> None:
    level = logging.DEBUG if app.config.get("DEBUG") else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
    )
    app.logger.setLevel(level)

    # These two dump every task signature at DEBUG, which buries the probe logs.
    for noisy in ("celery.utils.functional", "amqp"):
        logging.getLogger(noisy).setLevel(logging.INFO)


def _init_extensions(app: Flask) -> None:
    db.init_app(app)
    migrate.init_app(app, db, directory=os.path.join(app.root_path, "..", "migrations"))
    jwt.init_app(app)
    cors.init_app(app, resources={r"/api/*": {"origins": app.config["CORS_ORIGINS"]}})


def _register_jwt_handlers() -> None:
    """Make JWT rejections speak the same JSON envelope as everything else."""

    def _error(message: str, status: int):
        return jsonify({"error": {"message": message, "status": status}}), status

    @jwt.unauthorized_loader
    def _missing_token(reason):
        return _error(f"Authorization required: {reason}", 401)

    @jwt.invalid_token_loader
    def _invalid_token(reason):
        return _error(f"Invalid token: {reason}", 422)

    @jwt.expired_token_loader
    def _expired_token(_header, _payload):
        return _error("Token has expired", 401)


def _register_cli(app: Flask) -> None:
    import click

    @app.cli.command("seed-demo")
    @click.option("--email", default="admin@webguard.local", show_default=True)
    @click.option("--password", default="changeme123", show_default=True)
    @click.option("--org", "org_name", default="Demo Org", show_default=True)
    def seed_demo(email: str, password: str, org_name: str):
        """Create a demo organization, Admin user and one sample monitor."""
        from app.models import Monitor, Organization, User, UserRole

        if db.session.query(User.id).filter_by(email=email).first():
            click.echo(f"User {email} already exists — nothing to do.")
            return

        org = Organization(name=org_name, slug=Organization.slugify(org_name))
        user = User(organization=org, email=email, role=UserRole.ADMIN)
        user.set_password(password)
        monitor = Monitor(
            organization=org,
            name="Example",
            url="https://example.com",
            interval_seconds=300,
            timeout_seconds=10,
        )
        db.session.add_all([org, user, monitor])
        db.session.commit()
        click.echo(f"Seeded org '{org.slug}' with admin {email} / {password}")
