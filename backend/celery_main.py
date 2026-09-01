"""Celery entrypoint: ``celery -A celery_main:celery worker`` / ``... beat``.

The Celery instance is built by ``create_app`` so the web process and the
workers share one configuration and one task registry.
"""
from app import create_app

flask_app = create_app()
celery = flask_app.extensions["celery"]
