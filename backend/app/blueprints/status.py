"""Public status pages, and the tenant-facing CRUD that configures them.

Two blueprints with very different trust levels live here on purpose, so the
authentication boundary is visible in one file rather than spread across two:

* ``public_status_bp`` — no authentication at all. Every response is assembled
  by ``app.services.status``, never straight from a model.
* ``status_admin_bp`` — the usual JWT + tenant scoping.
"""
import uuid

from flask import Blueprint, Response, jsonify, render_template, request
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import RESERVED_SLUGS, Monitor, StatusPage, UserRole
from app.services import status as status_service
from app.utils.errors import APIError
from app.utils.tenancy import (
    auth_required,
    current_org_id,
    get_tenant_object_or_404,
    roles_required,
    tenant_query,
)
from app.utils.validators import clean_int, require_fields

public_status_bp = Blueprint("public_status", __name__)
status_admin_bp = Blueprint("status_admin", __name__)

MAX_PAGES_PER_ORG = 10

#: Browsers and CDNs may cache a status page briefly. Short enough that a new
#: outage shows up quickly, long enough to survive the traffic spike that an
#: outage itself produces — which is exactly when this page must stay up.
PUBLIC_CACHE_SECONDS = 30


# --- Public ---------------------------------------------------------------


def _render(page: StatusPage) -> Response:
    data = status_service.build_payload(page)
    response = Response(render_template("status.html", data=data), mimetype="text/html")
    response.headers["Cache-Control"] = f"public, max-age={PUBLIC_CACHE_SECONDS}"
    # This page embeds no scripts and loads nothing remote; say so, so a stored
    # XSS in a tenant-supplied field would have nothing to execute with.
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; base-uri 'none'; form-action 'none'"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    return response


@public_status_bp.get("/status/<slug>")
def public_page(slug: str):
    page = status_service.find_page(slug=slug)
    if page is None:
        # 404 either way: whether a slug exists but is unpublished is not
        # something an anonymous visitor gets to learn.
        return Response("Status page not found", status=404, mimetype="text/plain")
    return _render(page)


@public_status_bp.get("/status/<slug>.json")
def public_page_json(slug: str):
    page = status_service.find_page(slug=slug)
    if page is None:
        return jsonify({"error": {"message": "Status page not found", "status": 404}}), 404

    response = jsonify(status_service.build_payload(page))
    response.headers["Cache-Control"] = f"public, max-age={PUBLIC_CACHE_SECONDS}"
    # A published status page is meant to be embeddable anywhere.
    response.headers["Access-Control-Allow-Origin"] = "*"
    return response


def render_for_host(host: str) -> Response | None:
    """Serve a status page when the request arrives on a tenant's own CNAME.

    Called from the application root so a custom domain gets the status page
    while our own hostname keeps the service banner — a blueprint route for
    ``/`` would shadow the API root for everyone.
    """
    page = status_service.find_page(host=host)
    return _render(page) if page is not None else None


# --- Tenant administration ------------------------------------------------


def _parse_uuid(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise APIError("Malformed status page id", 400) from None


def _clean_slug(raw: str) -> str:
    slug = StatusPage.slugify(str(raw))
    if slug in RESERVED_SLUGS:
        raise APIError("That slug is reserved", 422, {"reserved": sorted(RESERVED_SLUGS)})
    if len(slug) < 2:
        raise APIError("Slug must be at least 2 characters", 422)
    return slug


def _clean_domain(raw) -> str | None:
    if raw in (None, ""):
        return None
    domain = str(raw).strip().lower().rstrip(".")
    if "/" in domain or ":" in domain or "." not in domain:
        raise APIError("custom_domain must be a bare hostname", 422)
    if len(domain) > 255:
        raise APIError("custom_domain is too long", 422)
    return domain


def _conflict(page: StatusPage, exclude_id=None) -> APIError:
    """Say which field actually collided.

    Three columns are unique here — name within the org, slug and domain
    globally — so a generic "already taken" sends the user hunting the wrong one.
    """
    query = db.session.query(StatusPage)
    if exclude_id is not None:
        query = query.filter(StatusPage.id != exclude_id)

    if query.filter(StatusPage.slug == page.slug).first():
        return APIError("That slug is already taken", 409, {"field": "slug"})
    if page.custom_domain and query.filter(
        StatusPage.custom_domain == page.custom_domain
    ).first():
        return APIError("That domain is already taken", 409, {"field": "custom_domain"})
    return APIError(
        "You already have a status page with this name", 409, {"field": "name"}
    )


def _clean_monitor_ids(raw) -> list[str] | None:
    """Keep only ids that belong to the caller's own organization."""
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise APIError("monitor_ids must be a list", 422)

    owned = {
        str(row[0]) for row in db.session.query(Monitor.id).filter(Monitor.org_id == current_org_id())
    }
    cleaned = []
    for value in raw:
        text = str(value)
        if text not in owned:
            raise APIError("Unknown monitor id", 422, {"monitor_id": text})
        cleaned.append(text)
    return cleaned


@status_admin_bp.get("")
@auth_required
def list_pages():
    rows = tenant_query(StatusPage).order_by(StatusPage.created_at.desc()).all()
    return jsonify({"status_pages": [p.to_dict() for p in rows]})


@status_admin_bp.post("")
@roles_required(UserRole.ADMIN, UserRole.ENGINEER)
def create_page():
    payload = require_fields(request.get_json(silent=True), "name")

    if tenant_query(StatusPage).count() >= MAX_PAGES_PER_ORG:
        raise APIError("Status page quota reached", 409, {"limit": MAX_PAGES_PER_ORG})

    page = StatusPage(
        org_id=current_org_id(),
        name=str(payload["name"]).strip(),
        slug=_clean_slug(payload.get("slug") or payload["name"]),
        custom_domain=_clean_domain(payload.get("custom_domain")),
        headline=payload.get("headline") or None,
        description=payload.get("description") or None,
        support_url=payload.get("support_url") or None,
        is_published=bool(payload.get("is_published", False)),
        history_days=clean_int(
            payload.get("history_days"), field="history_days", minimum=7, maximum=365, default=90
        ),
        show_urls=bool(payload.get("show_urls", False)),
        show_latency=bool(payload.get("show_latency", False)),
        show_incidents=bool(payload.get("show_incidents", True)),
        monitor_ids=_clean_monitor_ids(payload.get("monitor_ids")),
    )
    db.session.add(page)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        raise _conflict(page) from None

    return jsonify({"status_page": page.to_dict()}), 201


@status_admin_bp.get("/<page_id>")
@auth_required
def get_page(page_id: str):
    page = get_tenant_object_or_404(StatusPage, _parse_uuid(page_id))
    return jsonify({"status_page": page.to_dict()})


@status_admin_bp.patch("/<page_id>")
@roles_required(UserRole.ADMIN, UserRole.ENGINEER)
def update_page(page_id: str):
    page = get_tenant_object_or_404(StatusPage, _parse_uuid(page_id))
    payload = request.get_json(silent=True) or {}

    if "name" in payload:
        page.name = str(payload["name"]).strip()
    if "slug" in payload:
        page.slug = _clean_slug(payload["slug"])
    if "custom_domain" in payload:
        page.custom_domain = _clean_domain(payload["custom_domain"])
    if "headline" in payload:
        page.headline = payload["headline"] or None
    if "description" in payload:
        page.description = payload["description"] or None
    if "support_url" in payload:
        page.support_url = payload["support_url"] or None
    if "is_published" in payload:
        page.is_published = bool(payload["is_published"])
    if "history_days" in payload:
        page.history_days = clean_int(
            payload["history_days"],
            field="history_days",
            minimum=7,
            maximum=365,
            default=page.history_days,
        )
    for flag in ("show_urls", "show_latency", "show_incidents"):
        if flag in payload:
            setattr(page, flag, bool(payload[flag]))
    if "monitor_ids" in payload:
        page.monitor_ids = _clean_monitor_ids(payload["monitor_ids"])

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        raise _conflict(page, exclude_id=page.id) from None

    return jsonify({"status_page": page.to_dict()})


@status_admin_bp.delete("/<page_id>")
@roles_required(UserRole.ADMIN)
def delete_page(page_id: str):
    page = get_tenant_object_or_404(StatusPage, _parse_uuid(page_id))
    db.session.delete(page)
    db.session.commit()
    return "", 204


@status_admin_bp.get("/<page_id>/preview")
@auth_required
def preview_page(page_id: str):
    """Exactly what the public page would show, including while unpublished."""
    page = get_tenant_object_or_404(StatusPage, _parse_uuid(page_id))
    return jsonify(status_service.build_payload(page))
