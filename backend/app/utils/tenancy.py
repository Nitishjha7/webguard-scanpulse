"""Multi-tenant request context.

Every authenticated request binds ``g.current_user`` and ``g.org_id`` once, up
front. Data access then goes through :func:`tenant_query`, so a view can never
accidentally read another organization's rows.
"""
import uuid
from functools import wraps

from flask import Flask, g
from flask_jwt_extended import get_jwt_identity, verify_jwt_in_request
from flask_jwt_extended.exceptions import JWTExtendedException
from jwt import PyJWTError

from app.extensions import db
from app.models import User, UserRole
from app.utils.errors import APIError


def _load_current_user() -> User | None:
    """Resolve the JWT subject to a live, active user row. Returns None if the
    request carries no (or an unusable) token."""
    try:
        verify_jwt_in_request(optional=True)
    except (JWTExtendedException, PyJWTError):
        return None

    identity = get_jwt_identity()
    if not identity:
        return None

    try:
        user_id = uuid.UUID(str(identity))
    except ValueError:
        return None

    user = db.session.get(User, user_id)
    return user if user and user.is_active else None


def register_tenant_context(app: Flask) -> None:
    @app.before_request
    def _bind_tenant_context():
        user = _load_current_user()
        g.current_user = user
        g.org_id = user.org_id if user else None


def current_user() -> User:
    user = getattr(g, "current_user", None)
    if user is None:
        raise APIError("Authentication required", 401)
    return user


def current_org_id():
    return current_user().org_id


def auth_required(fn):
    """Reject the request unless a valid JWT resolved to an active user."""

    @wraps(fn)
    def wrapper(*args, **kwargs):
        current_user()
        return fn(*args, **kwargs)

    return wrapper


def roles_required(*roles: UserRole):
    """Reject the request unless the caller holds one of ``roles``."""

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user = current_user()
            if user.role not in roles:
                raise APIError(
                    "Insufficient permissions",
                    403,
                    {"required": [r.value for r in roles], "actual": user.role.value},
                )
            return fn(*args, **kwargs)

        return wrapper

    return decorator


def tenant_query(model):
    """Return a query for ``model`` pre-filtered to the caller's organization."""
    return db.session.query(model).filter(model.org_id == current_org_id())


def get_tenant_object_or_404(model, object_id):
    obj = tenant_query(model).filter(model.id == object_id).first()
    if obj is None:
        raise APIError(f"{model.__name__} not found", 404)
    return obj
