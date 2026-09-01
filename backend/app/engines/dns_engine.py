"""DNS posture and email-spoofing audit (SPF / DMARC / DNSSEC / CAA).

Scored out of 100 so it sits alongside the header grade on the same scale.
A DMARC record with ``p=none`` is monitoring-only and does not stop spoofing,
so it scores far below ``p=reject`` — presence alone is not protection.
"""
import logging
import os

import dns.exception
import dns.flags
import dns.rdatatype
import dns.resolver

logger = logging.getLogger(__name__)

DNS_TIMEOUT = 5.0

#: Query public recursive resolvers directly rather than whatever
#: /etc/resolv.conf points at. Inside Docker that is the embedded resolver at
#: 127.0.0.11, which does not proxy CAA or DNSKEY at all and returns NoAnswer
#: for apex TXT - every posture check would silently score zero.
DEFAULT_RESOLVERS = ("1.1.1.1", "8.8.8.8", "9.9.9.9")

DMARC_POLICY_POINTS = {"reject": 35, "quarantine": 25, "none": 10}
SPF_ALL_POINTS = {"-all": 30, "~all": 22, "?all": 8, "+all": 0}

GRADE_THRESHOLDS = ((90, "A+"), (80, "A"), (65, "B"), (50, "C"), (30, "D"))


def _resolver() -> dns.resolver.Resolver:
    resolver = dns.resolver.Resolver(configure=False)
    configured = os.getenv("DNS_RESOLVERS", "")
    resolver.nameservers = [
        ip.strip() for ip in configured.split(",") if ip.strip()
    ] or list(DEFAULT_RESOLVERS)
    resolver.timeout = DNS_TIMEOUT
    # lifetime covers retries across every nameserver, so give it room for one
    # resolver to time out before the next is tried.
    resolver.lifetime = DNS_TIMEOUT * len(resolver.nameservers)
    resolver.use_edns(0, dns.flags.DO, 4096)
    return resolver


def _query_txt(resolver, name: str) -> list[str]:
    """Return TXT strings for ``name``; empty list if the name has none."""
    try:
        answer = resolver.resolve(name, "TXT")
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.exception.Timeout):
        return []
    except dns.exception.DNSException as exc:
        logger.debug("TXT lookup failed for %s: %s", name, exc)
        return []

    records = []
    for rdata in answer:
        # A TXT record is a sequence of <255-byte chunks that must be concatenated.
        records.append(b"".join(rdata.strings).decode("utf-8", errors="replace"))
    return records


def _grade(score: int) -> str:
    for threshold, letter in GRADE_THRESHOLDS:
        if score >= threshold:
            return letter
    return "F"


def _audit_spf(records: list[str]) -> dict:
    spf = [r for r in records if r.lower().startswith("v=spf1")]
    if not spf:
        return {"present": False, "score": 0, "issue": "No SPF record published"}
    if len(spf) > 1:
        # Multiple SPF records are a hard failure per RFC 7208 — receivers permerror.
        return {"present": True, "record": spf, "score": 5, "issue": "Multiple SPF records (RFC 7208 permerror)"}

    record = spf[0]
    lowered = record.lower()
    qualifier = next((q for q in SPF_ALL_POINTS if q in lowered), None)
    if qualifier is None:
        return {"present": True, "record": record, "score": 12, "issue": "No 'all' mechanism — policy is open-ended"}

    issue = None
    if qualifier == "+all":
        issue = "'+all' permits any sender — worse than having no SPF"
    elif qualifier == "?all":
        issue = "'?all' is neutral and enforces nothing"
    elif qualifier == "~all":
        issue = "'~all' softfails; '-all' is the enforcing policy"

    return {"present": True, "record": record, "qualifier": qualifier, "score": SPF_ALL_POINTS[qualifier], "issue": issue}


def _audit_dmarc(records: list[str]) -> dict:
    dmarc = [r for r in records if r.lower().startswith("v=dmarc1")]
    if not dmarc:
        return {"present": False, "score": 0, "issue": "No DMARC record published"}

    record = dmarc[0]
    tags = {}
    for part in record.split(";"):
        if "=" in part:
            key, _, value = part.partition("=")
            tags[key.strip().lower()] = value.strip().lower()

    policy = tags.get("p", "none")
    score = DMARC_POLICY_POINTS.get(policy, 0)

    issue = None
    if policy == "none":
        issue = "p=none only monitors; it does not block spoofed mail"
    elif policy == "quarantine":
        issue = "p=quarantine sends spoofed mail to spam; p=reject blocks it"

    if tags.get("rua"):
        score += 5  # Aggregate reporting configured.
    elif issue is None:
        issue = "No rua= aggregate report address configured"

    return {"present": True, "record": record, "policy": policy, "tags": tags, "score": min(score, 40), "issue": issue}


def _audit_dnssec(resolver, domain: str) -> dict:
    """DNSSEC is present if the zone publishes a DNSKEY."""
    try:
        resolver.resolve(domain, "DNSKEY")
        return {"present": True, "score": 15, "issue": None}
    except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN):
        return {"present": False, "score": 0, "issue": "Zone is not DNSSEC signed"}
    except dns.exception.DNSException as exc:
        return {"present": False, "score": 0, "issue": f"DNSKEY lookup failed: {exc}"}


def _audit_caa(resolver, domain: str) -> dict:
    """CAA restricts which CAs may issue for the domain."""
    try:
        answer = resolver.resolve(domain, "CAA")
        issuers = [rdata.to_text() for rdata in answer]
        return {"present": True, "records": issuers, "score": 15, "issue": None}
    except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN):
        return {"present": False, "score": 0, "issue": "No CAA record — any CA may issue for this domain"}
    except dns.exception.DNSException as exc:
        return {"present": False, "score": 0, "issue": f"CAA lookup failed: {exc}"}


def audit_dns_posture(domain: str) -> dict:
    """Full DNS security audit for an apex domain."""
    domain = domain.strip().rstrip(".").lower()
    if not domain:
        return {"ok": False, "error": "Empty domain", "score": 0, "grade": "F"}

    resolver = _resolver()

    try:
        spf = _audit_spf(_query_txt(resolver, domain))
        dmarc = _audit_dmarc(_query_txt(resolver, f"_dmarc.{domain}"))
        dnssec = _audit_dnssec(resolver, domain)
        caa = _audit_caa(resolver, domain)
    except dns.exception.DNSException as exc:
        return {"ok": False, "error": f"DNS failure: {exc}", "score": 0, "grade": "F"}

    checks = {"spf": spf, "dmarc": dmarc, "dnssec": dnssec, "caa": caa}
    score = sum(c["score"] for c in checks.values())
    issues = [f"{name.upper()}: {c['issue']}" for name, c in checks.items() if c.get("issue")]

    return {
        "ok": True,
        "domain": domain,
        "score": score,
        "grade": _grade(score),
        "checks": checks,
        "issues": issues,
    }
