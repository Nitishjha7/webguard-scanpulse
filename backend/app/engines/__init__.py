"""Network inspection engines.

Every engine is a pure function: it takes a target, performs one kind of probe,
and returns a plain dict. No Flask context, no database — that keeps them unit
testable and reusable from Celery tasks, the CLI, or a future CLI scanner.

Each returns ``{"ok": bool, ...}``; on failure ``ok`` is False and ``error``
carries a human-readable reason. Engines never raise for network conditions —
only for programmer error.
"""
from app.engines.dns_engine import audit_dns_posture
from app.engines.header_engine import audit_security_headers
from app.engines.http_probe import probe_http
from app.engines.ssl_engine import inspect_ssl_certificate

__all__ = [
    "audit_dns_posture",
    "audit_security_headers",
    "probe_http",
    "inspect_ssl_certificate",
]
