"""Request payload validation helpers."""
from urllib.parse import urlparse

from email_validator import EmailNotValidError, validate_email

from app.utils.errors import APIError


def require_fields(payload: dict | None, *fields: str) -> dict:
    if not isinstance(payload, dict):
        raise APIError("Request body must be a JSON object", 400)
    missing = [f for f in fields if payload.get(f) in (None, "")]
    if missing:
        raise APIError("Missing required fields", 422, {"fields": missing})
    return payload


def clean_email(raw: str, *, allow_reserved: bool = False) -> str:
    """Normalise and validate an email address.

    ``allow_reserved`` permits special-use domains (``.local``, ``.test``,
    ``example.com``). Sign-up keeps them out, but *lookup* paths must accept
    them: an account that already exists has to stay able to log in, and the
    seeded demo admin lives at ``.local``.
    """
    try:
        return validate_email(
            raw, check_deliverability=False, test_environment=allow_reserved
        ).normalized.lower()
    except EmailNotValidError as exc:
        raise APIError(f"Invalid email address: {exc}", 422) from exc


def clean_password(raw: str) -> str:
    if len(raw) < 8:
        raise APIError("Password must be at least 8 characters", 422)
    return raw


def clean_monitor_url(raw: str) -> str:
    """Accept only absolute http(s) URLs with a hostname.

    Note: this is *syntactic* validation only. SSRF resolution guards
    (loopback / RFC1918 / cloud-metadata rejection) land with the probe
    engines in Phase 2, since they must run at connect time.
    """
    parsed = urlparse(raw.strip())
    if parsed.scheme not in {"http", "https"}:
        raise APIError("URL must start with http:// or https://", 422)
    if not parsed.hostname:
        raise APIError("URL must include a hostname", 422)
    return parsed.geturl()


def clean_int(value, *, field: str, minimum: int, maximum: int, default: int) -> int:
    if value is None:
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise APIError(f"{field} must be an integer", 422) from None
    if not minimum <= number <= maximum:
        raise APIError(f"{field} must be between {minimum} and {maximum}", 422)
    return number
