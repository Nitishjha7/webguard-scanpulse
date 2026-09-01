"""Celery tasks. Importing this module registers every task on the shared
Celery instance created by ``create_app``.
"""
from app.tasks import alerts, probes, scheduler  # noqa: F401

__all__ = ["alerts", "probes", "scheduler"]
