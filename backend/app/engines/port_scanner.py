"""Exposed-port scanner.

A TCP connect scan over a fixed list of ports that should not be reachable from
the public internet. Deliberately *not* a general-purpose port scanner: the
list is fixed, the host must be one the tenant already monitors, and the target
goes through the same SSRF guard as every other probe, so this cannot be
pointed at internal infrastructure.

Asyncio rather than threads because the work is almost entirely waiting: a few
dozen sockets that mostly time out is the exact shape asyncio is good at.
"""
import asyncio
import logging

from app.utils.ssrf import SSRFError, resolve_safe_addresses

logger = logging.getLogger(__name__)

#: Per-port connect timeout. A filtered port simply never answers, so this is
#: the dominant cost of a scan — long enough not to miss a slow host, short
#: enough that the whole sweep fits inside a Celery soft time limit.
CONNECT_TIMEOUT = 3.0

#: Ceiling on simultaneous sockets, so one scan cannot exhaust the worker's
#: file descriptors or look like a SYN flood to the target.
MAX_CONCURRENCY = 20


class Severity:
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    INFO = "info"


#: port -> (service, severity, why it matters)
WATCHED_PORTS: dict[int, tuple[str, str, str]] = {
    # Datastores. Reachable from the internet means the data is one weak
    # password — or no password at all — from being taken.
    6379: ("Redis", Severity.CRITICAL, "Redis defaults to no authentication"),
    27017: ("MongoDB", Severity.CRITICAL, "Database directly exposed"),
    9200: ("Elasticsearch", Severity.CRITICAL, "Cluster API exposed, often unauthenticated"),
    11211: ("Memcached", Severity.CRITICAL, "No authentication, and a UDP amplification vector"),
    5432: ("PostgreSQL", Severity.CRITICAL, "Database directly exposed"),
    3306: ("MySQL", Severity.CRITICAL, "Database directly exposed"),
    5984: ("CouchDB", Severity.CRITICAL, "Database HTTP API exposed"),
    1433: ("MS SQL Server", Severity.CRITICAL, "Database directly exposed"),
    # Plaintext or remote-access protocols.
    23: ("Telnet", Severity.CRITICAL, "Credentials travel in plaintext"),
    21: ("FTP", Severity.HIGH, "Credentials travel in plaintext"),
    3389: ("RDP", Severity.HIGH, "Frequent brute-force and exploit target"),
    25: ("SMTP", Severity.HIGH, "Verify this is not an open relay"),
    22: ("SSH", Severity.MEDIUM, "Expected on many hosts; keys-only is strongly advised"),
    # Admin surfaces that are usually meant to sit behind the proxy.
    8080: ("HTTP alt", Severity.MEDIUM, "Often an unprotected admin or origin port"),
    8443: ("HTTPS alt", Severity.MEDIUM, "Often an unprotected admin port"),
    9090: ("Prometheus/admin", Severity.MEDIUM, "Metrics endpoints leak internal topology"),
    2375: ("Docker API", Severity.CRITICAL, "Unauthenticated Docker API is remote root"),
    # Expected, reported for completeness.
    80: ("HTTP", Severity.INFO, "Expected for a web host"),
    443: ("HTTPS", Severity.INFO, "Expected for a web host"),
}

SEVERITY_PENALTY = {
    Severity.CRITICAL: 40,
    Severity.HIGH: 20,
    Severity.MEDIUM: 5,
    Severity.INFO: 0,
}

GRADE_THRESHOLDS = ((90, "A+"), (80, "A"), (65, "B"), (50, "C"), (30, "D"))


def _grade(score: int) -> str:
    for threshold, letter in GRADE_THRESHOLDS:
        if score >= threshold:
            return letter
    return "F"


async def _probe_port(host: str, port: int, semaphore: asyncio.Semaphore) -> tuple[int, bool]:
    """One TCP connect. Open means the handshake completed."""
    async with semaphore:
        writer = None
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=CONNECT_TIMEOUT
            )
            return port, True
        except (asyncio.TimeoutError, ConnectionRefusedError, OSError):
            # Refused and filtered are both "not exposed" for our purposes; the
            # distinction only matters to someone mapping a firewall.
            return port, False
        finally:
            if writer is not None:
                writer.close()
                try:
                    await writer.wait_closed()
                except (OSError, asyncio.TimeoutError):
                    pass


async def _scan(host: str, ports: list[int]) -> list[int]:
    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
    results = await asyncio.gather(*(_probe_port(host, p, semaphore) for p in ports))
    return sorted(port for port, is_open in results if is_open)


def scan_ports(hostname: str, ports: list[int] | None = None) -> dict:
    """Scan ``hostname`` for exposed services.

    Returns ``{"ok": bool, "open_ports": [...], "score": int | None, "grade": str | None}``.
    Like every other engine, network conditions come back as ``ok: False`` with
    an ``error`` rather than as an exception.
    """
    ports = sorted(ports or WATCHED_PORTS)
    # score/grade stay null until a scan actually completes — a failed scan that
    # reported 100/A+ would read as "nothing exposed" rather than "did not run".
    result = {"ok": False, "hostname": hostname, "open_ports": [], "score": None, "grade": None}

    try:
        # Resolve first: the scan must never reach a private address, and we
        # connect to the resolved IP so a rebind between check and connect
        # cannot redirect it.
        addresses = resolve_safe_addresses(hostname, 443)
    except SSRFError as exc:
        result["error"] = f"blocked: {exc}"
        return result

    target = addresses[0]

    try:
        open_ports = asyncio.run(_scan(target, ports))
    except OSError as exc:
        result["error"] = f"{exc.__class__.__name__}: {exc}"
        return result

    findings = []
    score = 100
    for port in open_ports:
        service, severity, note = WATCHED_PORTS.get(port, ("Unknown", Severity.MEDIUM, ""))
        findings.append(
            {"port": port, "service": service, "severity": severity, "note": note}
        )
        score -= SEVERITY_PENALTY[severity]

    score = max(0, score)
    result.update(
        {
            "ok": True,
            "scanned_address": target,
            "ports_scanned": len(ports),
            "open_ports": open_ports,
            "findings": findings,
            "score": score,
            "grade": _grade(score),
            "critical_count": sum(1 for f in findings if f["severity"] == Severity.CRITICAL),
        }
    )

    if findings:
        logger.warning(
            "port scan %s: %d open (%s)",
            hostname,
            len(findings),
            ", ".join(f"{f['port']}/{f['service']}" for f in findings),
        )
    return result
