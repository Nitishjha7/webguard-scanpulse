"""Registration, login and token refresh.

Registration creates the organization *and* its first Admin in one transaction —
there is no way to have a tenant without an owner.
"""
from flask import Blueprint, jsonify, request
from flask_jwt_extended import (
    create_access_token,
    create_refresh_token,
    get_jwt_identity,
    jwt_required,
)
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Organization, User, UserRole
from app.utils.errors import APIError
from app.utils.tenancy import auth_required, current_user, roles_required
from app.utils.validators import (
    clean_email,
    clean_password,
    normalize_email_for_lookup,
    require_fields,
)

auth_bp = Blueprint("auth", __name__)


def _issue_tokens(user: User) -> dict:
    claims = {"org_id": str(user.org_id), "role": user.role.value}
    identity = str(user.id)
    return {
        "access_token": create_access_token(identity=identity, additional_claims=claims),
        "refresh_token": create_refresh_token(identity=identity, additional_claims=claims),
    }


def _unique_slug(name: str) -> str:
    base = Organization.slugify(name)
    slug, suffix = base, 2
    while db.session.query(Organization.id).filter_by(slug=slug).first():
        slug = f"{base}-{suffix}"
        suffix += 1
    return slug


@auth_bp.post("/register")
def register():
    """Create a new tenant plus its owning Admin user."""
    payload = require_fields(request.get_json(silent=True), "email", "password", "org_name")
    email = clean_email(payload["email"])
    password = clean_password(payload["password"])

    if db.session.query(User.id).filter_by(email=email).first():
        raise APIError("An account with this email already exists", 409)

    org = Organization(name=payload["org_name"].strip(), slug=_unique_slug(payload["org_name"]))
    user = User(organization=org, email=email, role=UserRole.ADMIN)
    user.set_password(password)

    db.session.add(org)
    db.session.add(user)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        raise APIError("An account with this email already exists", 409) from None

    return jsonify({"organization": org.to_dict(), "user": user.to_dict(), **_issue_tokens(user)}), 201


@auth_bp.post("/login")
def login():
    payload = require_fields(request.get_json(silent=True), "email", "password")
    email = normalize_email_for_lookup(payload["email"])

    user = db.session.query(User).filter_by(email=email).first()
    # Same message either way, so the response cannot enumerate valid emails.
    if user is None or not user.check_password(payload["password"]):
        raise APIError("Invalid email or password", 401)
    if not user.is_active:
        raise APIError("This account is disabled", 403)

    return jsonify({"user": user.to_dict(), **_issue_tokens(user)})


@auth_bp.post("/refresh")
@jwt_required(refresh=True)
def refresh():
    import uuid as _uuid

    user = db.session.get(User, _uuid.UUID(str(get_jwt_identity())))
    if user is None or not user.is_active:
        raise APIError("Account no longer active", 401)

    claims = {"org_id": str(user.org_id), "role": user.role.value}
    token = create_access_token(identity=str(user.id), additional_claims=claims)
    return jsonify({"access_token": token})


@auth_bp.get("/me")
@auth_required
def me():
    user = current_user()
    return jsonify({"user": user.to_dict(), "organization": user.organization.to_dict()})


@auth_bp.post("/users")
@roles_required(UserRole.ADMIN)
def invite_user():
    """Admin adds another member to *their own* organization."""
    payload = require_fields(request.get_json(silent=True), "email", "password")
    email = clean_email(payload["email"])
    password = clean_password(payload["password"])

    try:
        role = UserRole(payload.get("role", UserRole.VIEWER.value))
    except ValueError:
        raise APIError(
            "Unknown role", 422, {"allowed": [r.value for r in UserRole]}
        ) from None

    if db.session.query(User.id).filter_by(email=email).first():
        raise APIError("An account with this email already exists", 409)

    member = User(org_id=current_user().org_id, email=email, role=role)
    member.set_password(password)
    db.session.add(member)
    db.session.commit()

    return jsonify({"user": member.to_dict()}), 201
