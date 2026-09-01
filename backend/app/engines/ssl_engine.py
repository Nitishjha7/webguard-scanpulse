"""SSL/TLS handshake and X.509 certificate inspection.

Performs a real TLS handshake against the target, pulls the DER-encoded leaf
certificate off the socket and decodes it, rather than trusting any third-party
API. Expiry is computed in UTC against the certificate's own notAfter.
"""
import logging
import socket
import ssl
from datetime import datetime, timezone

from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes

from app.utils.ssrf import SSRFError, resolve_safe_addresses

logger = logging.getLogger(__name__)

#: Anything below TLS 1.2 is considered deprecated by every modern baseline.
DEPRECATED_TLS = frozenset({"SSLv2", "SSLv3", "TLSv1", "TLSv1.1"})


def _utc(value: datetime) -> datetime:
    """cryptography >= 42 exposes ``*_utc``; normalise either shape to aware UTC."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _not_after(cert) -> datetime:
    return _utc(getattr(cert, "not_valid_after_utc", None) or cert.not_valid_after)


def _not_before(cert) -> datetime:
    return _utc(getattr(cert, "not_valid_before_utc", None) or cert.not_valid_before)


def _subject_alt_names(cert) -> list[str]:
    try:
        ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        return ext.value.get_values_for_type(x509.DNSName)
    except x509.ExtensionNotFound:
        return []


def inspect_ssl_certificate(hostname: str, port: int = 443, timeout: int = 10) -> dict:
    """Handshake with ``hostname:port`` and decode the presented certificate.

    A certificate that fails verification (expired, self-signed, wrong host) is
    still worth reporting on, so on verification failure we retry once with
    verification disabled and record *why* it failed. That distinction — chain
    invalid vs. host unreachable — is the whole point of the scan.
    """
    result = {
        "ok": False,
        "hostname": hostname,
        "port": port,
        "is_valid": False,
        "verify_error": None,
        "error": None,
    }

    try:
        resolve_safe_addresses(hostname, port)
    except SSRFError as exc:
        result["error"] = f"blocked: {exc}"
        return result

    verify_error = None
    try:
        der, tls_version, cipher = _handshake(hostname, port, timeout, verify=True)
    except ssl.SSLCertVerificationError as exc:
        verify_error = exc.verify_message or str(exc)
        try:
            der, tls_version, cipher = _handshake(hostname, port, timeout, verify=False)
        except (OSError, ssl.SSLError) as inner:
            result["error"] = f"{inner.__class__.__name__}: {inner}"
            return result
    except (OSError, ssl.SSLError) as exc:
        result["error"] = f"{exc.__class__.__name__}: {exc}"
        return result

    cert = x509.load_der_x509_certificate(der, default_backend())
    valid_to = _not_after(cert)
    valid_from = _not_before(cert)
    days_left = (valid_to - datetime.now(timezone.utc)).days

    result.update(
        {
            "ok": True,
            "issuer": cert.issuer.rfc4514_string(),
            "subject": cert.subject.rfc4514_string(),
            "serial_number": format(cert.serial_number, "x"),
            "fingerprint_sha256": cert.fingerprint(hashes.SHA256()).hex(),
            "san": _subject_alt_names(cert),
            "valid_from": valid_from.isoformat(),
            "valid_to": valid_to.isoformat(),
            "days_left": days_left,
            "tls_version": tls_version,
            "cipher": cipher[0] if cipher else None,
            "is_expired": days_left < 0,
            "is_deprecated_tls": tls_version in DEPRECATED_TLS,
            "verify_error": verify_error,
            # Valid means: chain verified, in its validity window, modern TLS.
            "is_valid": verify_error is None and days_left >= 0 and tls_version not in DEPRECATED_TLS,
        }
    )
    return result


def _handshake(hostname: str, port: int, timeout: int, *, verify: bool):
    context = ssl.create_default_context()
    if verify:
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
    else:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

    with socket.create_connection((hostname, port), timeout=timeout) as sock:
        with context.wrap_socket(sock, server_hostname=hostname) as ssock:
            return ssock.getpeercert(binary_form=True), ssock.version(), ssock.cipher()
