"""SSRF guards for outbound probes.

Users control the target URLs we connect to, so every probe resolves the
hostname *first* and refuses anything that lands on infrastructure the worker
can reach but the public internet cannot: loopback, RFC1918, link-local (which
covers the 169.254.169.254 cloud metadata endpoint), CGNAT and reserved space.

The guard rejects if *any* resolved address is unsafe, not just the first — a
hostname with one public and one private A record is still an attack.
"""
import ipaddress
import socket
from urllib.parse import urlparse

#: Blocked destination ports — probes are for web endpoints, not arbitrary services.
ALLOWED_SCHEMES = frozenset({"http", "https"})
DEFAULT_PORTS = {"http": 80, "https": 443}


class SSRFError(ValueError):
    """Raised when a target resolves to a non-public address."""


def _is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if ip.is_private or ip.is_loopback or ip.is_link_local:
        return False
    if ip.is_reserved or ip.is_multicast or ip.is_unspecified:
        return False
    if isinstance(ip, ipaddress.IPv4Address):
        # 100.64.0.0/10 carrier-grade NAT — not flagged private by ipaddress.
        if ip in ipaddress.ip_network("100.64.0.0/10"):
            return False
    else:
        # IPv4-mapped (::ffff:127.0.0.1) would otherwise slip through.
        if ip.ipv4_mapped is not None:
            return _is_public(ip.ipv4_mapped)
        if ip.is_site_local:
            return False
    return True


def resolve_safe_addresses(hostname: str, port: int) -> list[str]:
    """Resolve ``hostname`` and return its addresses, or raise :class:`SSRFError`.

    Returns the resolved IPs so callers can log exactly what was probed.
    """
    try:
        infos = socket.getaddrinfo(hostname, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise SSRFError(f"DNS resolution failed for {hostname}: {exc}") from exc

    if not infos:
        raise SSRFError(f"No addresses resolved for {hostname}")

    addresses = []
    for info in infos:
        raw = info[4][0]
        ip = ipaddress.ip_address(raw)
        if not _is_public(ip):
            raise SSRFError(f"{hostname} resolves to non-public address {raw}")
        addresses.append(raw)
    return addresses


def assert_safe_url(url: str) -> tuple[str, int, list[str]]:
    """Validate a full URL end to end.

    Returns ``(hostname, port, resolved_addresses)``.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise SSRFError(f"Unsupported scheme: {parsed.scheme or '(none)'}")
    if not parsed.hostname:
        raise SSRFError("URL has no hostname")

    port = parsed.port or DEFAULT_PORTS[parsed.scheme]
    return parsed.hostname, port, resolve_safe_addresses(parsed.hostname, port)
