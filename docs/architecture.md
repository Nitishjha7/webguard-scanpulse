# WebGuard (ScanPulse) — Comprehensive Architecture, Technical Specification & Phase-Wise Roadmap

**Automated Website Health, SSL Certificate Inspection, DNS Threat Posture & Synthetic Monitoring Engine**

## 1. Executive Summary & Project Vision

WebGuard (alternatively known as ScanPulse) is an enterprise-grade, distributed website observability and security compliance platform. It bridges the gap between conventional uptime monitors (e.g., UptimeRobot) and deep protocol-level security evaluators (e.g., SSL Labs, SecurityHeaders.io). Designed with high-throughput asynchronous network probing and a multi-tenant relational backend, WebGuard continuously validates uptime latency, SSL/TLS cipher integrity, DNS security configurations (SPF/DKIM/DMARC), exposed port footprints, and synthetic end-to-end user browser interactions.

## 2. Architectural Justification & Tech Stack Matrix

The system is engineered using a modular Flask Application Factory pattern coupled with distributed Celery workers and Redis. Flask offers complete transparency across the WSGI request lifecycle through explicit hooks (`@app.before_request`, `@app.teardown_appcontext`), allowing multi-tenant context binding and granular database session pooling without framework bloat.

| Component Layer | Selected Technology | Technical Purpose & Rationale |
|---|---|---|
| Backend API & App Factory | Python 3.11+ / Flask / Flask-JWT-Extended | Modular Blueprint architecture, lightweight routing, strict lifecycle control, tenant-scoped request context. |
| Asynchronous Task & Scheduler | Celery & Celery Beat | Periodic dispatching of sub-minute health pings, deep security audits, and multi-region quorum consensus checks. |
| Message Broker & Cache | Redis 7.x | High-throughput task queueing, state caching, distributed rate-limiting, and ephemeral incident locks. |
| Primary Relational Database | PostgreSQL 16 / TimescaleDB | Multi-tenant relational schema with monthly table partitioning and continuous downsampling for time-series ping metrics. |
| Network Inspection Engine | cryptography, dnspython, socket, ssl, requests | Low-level socket TLS inspection, X.509 certificate decoding, DNS record validation, and HTTP security header audits. |
| Synthetic E2E Monitoring | Playwright (Headless Chromium) | Automates critical multi-step user transactions (e.g., authentication, checkout flows) with automated error screenshots. |
| Frontend Dashboard & Charts | React (Vite), Tailwind CSS, Chart.js / Recharts | Real-time latency charts, interactive security scorecards (A+ to F), incident resolution boards, and monitor controls. |
| Notification & Dispatchers | SendGrid, Twilio, Slack & Discord Webhooks | Multi-channel instant alerting on incident state shifts (DOWN, DEGRADED, RECOVERED) and SSL expiration countdowns. |

## 3. Core Relational & Time-Series Data Models

The database architecture enforces strict multi-tenancy at the relational layer while segregating high-velocity time-series ping events from transactional incident logs.

| Table Name | Primary Columns & Types | Constraints & Indexes |
|---|---|---|
| organizations (Tenants) | id (UUID), name (VARCHAR), slug (VARCHAR), created_at (TIMESTAMP) | UNIQUE(slug), Primary Tenant Partition Key |
| users | id (UUID), org_id (FK), email (VARCHAR), password_hash (VARCHAR), role (ENUM: Admin, Engineer, Viewer) | UNIQUE(email), INDEX(org_id) |
| monitors | id (UUID), org_id (FK), url (VARCHAR), interval_seconds (INT), timeout_seconds (INT), is_active (BOOL) | INDEX(org_id), INDEX(is_active) |
| ping_logs (Partitioned) | id (BIGINT), monitor_id (FK), region (VARCHAR), status_code (INT), latency_ms (FLOAT), is_up (BOOL), checked_at (TIMESTAMP) | PARTITION BY RANGE (checked_at), COMPOSITE INDEX(monitor_id, checked_at DESC) |
| ssl_scans | id (UUID), monitor_id (FK), issuer (VARCHAR), valid_from (TIMESTAMP), valid_to (TIMESTAMP), days_left (INT), tls_version (VARCHAR), is_valid (BOOL) | INDEX(monitor_id), INDEX(days_left) |
| security_audits | id (UUID), monitor_id (FK), score (INT), grade (VARCHAR), headers_payload (JSONB), open_ports (JSONB), scanned_at (TIMESTAMP) | INDEX(monitor_id), GIN INDEX(headers_payload) |
| incidents | id (UUID), monitor_id (FK), status (ENUM: DOWN, DEGRADED, RESOLVED), started_at (TIMESTAMP), resolved_at (TIMESTAMP), root_cause (TEXT) | INDEX(monitor_id), INDEX(status) |

## 4. Deep Inspection & Verification Engine Modules

### 4.1 SSL/TLS Handshake & Certificate Expiry Engine

Performs a direct cryptographic handshake with the target domain's port 443, extracts the raw DER-encoded certificate, parses the X.509 structure, and computes exact UTC expiry boundaries and cipher parameters.

```python
import socket
import ssl
from datetime import datetime, timezone
from cryptography import x509
from cryptography.hazmat.backends import default_backend

def inspect_ssl_certificate(hostname: str, port: int = 443) -> dict:
    context = ssl.create_default_context()
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED

    with socket.create_connection((hostname, port), timeout=5) as sock:
        with context.wrap_socket(sock, server_hostname=hostname) as ssock:
            der_cert = ssock.getpeercert(binary_form=True)
            cert = x509.load_der_x509_certificate(der_cert, default_backend())
            tls_version = ssock.version()

    valid_to = cert.not_valid_after_utc
    days_left = (valid_to - datetime.now(timezone.utc)).days

    return {
        "issuer": cert.issuer.rfc4514_string(),
        "subject": cert.subject.rfc4514_string(),
        "valid_from": cert.not_valid_before_utc.isoformat(),
        "valid_to": valid_to.isoformat(),
        "days_left": days_left,
        "tls_version": tls_version,
        "is_valid": days_left > 0
    }
```

### 4.2 HTTP Security Headers & Weighted Scoring Engine

Evaluates mandatory defensive HTTP response headers against a strict 100-point security rubric, generating automated letter grades from A+ to F.

```python
import requests

HEADER_WEIGHTS = {
    "Strict-Transport-Security": 25,
    "Content-Security-Policy": 25,
    "X-Frame-Options": 20,
    "X-Content-Type-Options": 15,
    "Referrer-Policy": 15
}

def audit_security_headers(target_url: str) -> dict:
    resp = requests.get(target_url, timeout=5, allow_redirects=True)
    headers = {k.lower(): v for k, v in resp.headers.items()}

    score = 0
    missing = []
    present = {}

    for header, weight in HEADER_WEIGHTS.items():
        h_key = header.lower()
        if h_key in headers:
            score += weight
            present[header] = headers[h_key]
        else:
            missing.append(header)

    grade = "F"
    if score >= 90: grade = "A+"
    elif score >= 80: grade = "A"
    elif score >= 65: grade = "B"
    elif score >= 50: grade = "C"

    return {"score": score, "grade": grade, "present": present, "missing": missing}
```

### 4.3 DNS Posture & Email Spoofing Audit (SPF / DMARC)

Utilizes dnspython to query authoritative nameservers for SPF policy records, DMARC alignment rules (`p=reject` vs `p=none`), and DNSSEC cryptographic signature validity.

## 5. Phase-Wise Implementation Roadmap & Milestones

The project is structured into 6 sequential phases to ensure seamless component integration and rigorous test coverage.

| Phase | Milestone Focus | Key Deliverables & Architectural Objectives | Git Branch Name |
|---|---|---|---|
| Phase 1 | Core Scaffold, Multi-Tenancy & Models | Flask App Factory pattern, JWT authentication, Tenant isolation middleware, Alembic migrations, PostgreSQL schema setup. | feature/phase-1-flask-scaffold |
| Phase 2 | Inspection Engines & Background Workers | Standalone SSL, Header, and DNS audit scripts; Celery worker setup with Redis broker; Celery Beat periodic HTTP pinger. | feature/phase-2-scan-engines |
| Phase 3 | Quorum Alerting & Incident State Machine | 2-strike failure verification heuristics, Incident state machine (UP → DOWN → RESOLVED), Slack/Discord webhook & SendGrid dispatchers. | feature/phase-3-incident-alerts |
| Phase 4 | Synthetic E2E & Port Scanning | Playwright headless browser execution for multi-step transaction monitoring; Asyncio socket port scanner for exposed ports (21, 22, 3306, 6379). | feature/phase-4-synthetic-monitor |
| Phase 5 | Time-Series Optimization & Public Status Pages | PostgreSQL table partitioning on ping_logs; TimescaleDB downsampling policy (1-min → 1-hour → daily); Jinja2 public status page generator with custom CNAME support. | feature/phase-5-db-optimization |
| Phase 6 | React Dashboard, Visualization & Dockerization | React (Vite) + Tailwind CSS dashboard; Chart.js latency graphs; Multi-service Docker Compose deployment (Flask, Celery Worker, Beat, Redis, Postgres). | feature/phase-6-frontend-docker |

## 6. Production Hardening & Operational Resilience

- **Server-Side Request Forgery (SSRF) Prevention:** All target monitor URLs are resolved via DNS prior to socket connection. Any hostname resolving to loopback (127.0.0.0/8), private RFC1918 subnets (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16), or cloud metadata endpoints (169.254.169.254) are rejected immediately.
- **Anti-Flapping Quorum Heuristics:** Network blips do not immediately dispatch high-priority SMS/Email alerts. A target must register failed probes across at least 2 distinct worker iterations or 2 separate geographical regions before transitioning to DOWN.
- **Continuous Downsampling & Retention Policies:** Raw 1-minute ping logs are automatically condensed into hourly rollups after 7 days, and hourly rollups are aggregated into daily uptime metrics after 90 days, preserving database query speeds.
