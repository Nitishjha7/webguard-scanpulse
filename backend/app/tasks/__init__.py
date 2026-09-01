"""Celery tasks. Importing this module registers every task on the shared
Celery instance created by ``create_app``.
"""
from app.tasks import probes, scheduler  # noqa: F401

__all__ = ["probes", "scheduler"]
