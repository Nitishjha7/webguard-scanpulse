"""Celery integration.

Tasks need the Flask app context (config, SQLAlchemy session), so every task
runs inside ``app.app_context()`` via a custom base class. The Celery instance
is created at import time so ``celery -A celery_main:celery`` can find it.
"""
import logging
from datetime import timedelta

from celery import Celery, Task
from celery.signals import worker_ready

logger = logging.getLogger(__name__)

#: How often beat asks "which monitors are due?" — dispatch is cheap, the
#: per-monitor interval is enforced inside the dispatcher, not here.
DISPATCH_INTERVAL_SECONDS = 30


def make_celery(flask_app) -> Celery:
    class ContextTask(Task):
        def __call__(self, *args, **kwargs):
            with flask_app.app_context():
                return self.run(*args, **kwargs)

    celery = Celery(
        flask_app.import_name,
        broker=flask_app.config["REDIS_URL"],
        backend=flask_app.config["REDIS_URL"],
        task_cls=ContextTask,
    )

    celery.conf.update(
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone="UTC",
        enable_utc=True,
        # Probes are I/O bound and idempotent; fetching one at a time keeps a
        # slow target from blocking a prefetched queue of fast ones.
        worker_prefetch_multiplier=1,
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        # A probe that outlives its own interval is useless — kill it.
        task_soft_time_limit=120,
        task_time_limit=180,
        result_expires=timedelta(hours=6),
        broker_connection_retry_on_startup=True,
        task_routes={
            "webguard.probe_monitor": {"queue": "probes"},
            "webguard.scan_monitor_security": {"queue": "scans"},
        },
        task_default_queue="probes",
        beat_schedule={
            "dispatch-due-pings": {
                "task": "webguard.dispatch_due_pings",
                "schedule": float(DISPATCH_INTERVAL_SECONDS),
            },
            "dispatch-daily-security-scans": {
                "task": "webguard.dispatch_due_security_scans",
                "schedule": 3600.0,
            },
        },
    )

    celery.flask_app = flask_app
    return celery


@worker_ready.connect
def _log_ready(sender, **_kwargs):
    logger.info("Celery worker ready: %s", sender.hostname)
