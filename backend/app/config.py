"""Environment-driven configuration classes."""
import os
from datetime import timedelta


def _bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


class BaseConfig:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-insecure-change-me")

    SQLALCHEMY_DATABASE_URI = os.getenv(
        "DATABASE_URL", "postgresql+psycopg2://webguard:webguard@postgres:5432/webguard"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_size": int(os.getenv("DB_POOL_SIZE", "10")),
        "max_overflow": int(os.getenv("DB_MAX_OVERFLOW", "20")),
    }

    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", SECRET_KEY)
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(
        minutes=int(os.getenv("JWT_ACCESS_MINUTES", "60"))
    )
    JWT_REFRESH_TOKEN_EXPIRES = timedelta(
        days=int(os.getenv("JWT_REFRESH_DAYS", "30"))
    )

    # Broker / cache — consumed by Celery in Phase 2.
    REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

    CORS_ORIGINS = [
        o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()
    ]

    # Monitor guardrails (enforced by the probe engines in Phase 2).
    MIN_INTERVAL_SECONDS = int(os.getenv("MIN_INTERVAL_SECONDS", "60"))
    MAX_MONITORS_PER_ORG = int(os.getenv("MAX_MONITORS_PER_ORG", "100"))

    # --- Alerting (Phase 3) ----------------------------------------------
    SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY", "")
    ALERT_FROM_EMAIL = os.getenv("ALERT_FROM_EMAIL", "alerts@webguard.invalid")
    ALERT_FROM_NAME = os.getenv("ALERT_FROM_NAME", "WebGuard ScanPulse")

    #: Consecutive failed probes before an incident opens. One blip is not an
    #: outage, so the floor is 2.
    DEFAULT_FAILURE_THRESHOLD = max(2, int(os.getenv("DEFAULT_FAILURE_THRESHOLD", "2")))

    #: Days-remaining marks that trigger a certificate expiry warning. Each
    #: monitor is alerted at most once per mark, so crossing 30 does not then
    #: re-alert every day until 14.
    SSL_EXPIRY_THRESHOLDS = sorted(
        (
            int(d)
            for d in os.getenv("SSL_EXPIRY_THRESHOLDS", "30,14,7,3,1").split(",")
            if d.strip()
        ),
        reverse=True,
    )


class DevelopmentConfig(BaseConfig):
    DEBUG = True
    SQLALCHEMY_ECHO = _bool("SQLALCHEMY_ECHO")


class TestingConfig(BaseConfig):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = os.getenv("TEST_DATABASE_URL", "sqlite:///:memory:")
    SQLALCHEMY_ENGINE_OPTIONS = {}


class ProductionConfig(BaseConfig):
    DEBUG = False


CONFIG_MAP = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}


def get_config(name: str | None = None):
    key = (name or os.getenv("FLASK_ENV", "development")).lower()
    return CONFIG_MAP.get(key, DevelopmentConfig)
