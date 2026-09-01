# Phase 2 — Inspection Engines & Background Workers

Committed directly to `main` — single-developer workflow, no feature branches.

## What shipped

| Area | Files |
|---|---|
| SSRF guard | `backend/app/utils/ssrf.py` |
| Engines | `backend/app/engines/{http_probe,ssl_engine,header_engine,dns_engine}.py` |
| Result models | `backend/app/models/scans.py` (`ping_logs`, `ssl_scans`, `security_audits`) |
| Celery wiring | `backend/app/celery_app.py`, `backend/celery_main.py` |
| Tasks | `backend/app/tasks/{probes,scheduler}.py` |
| Result endpoints | `backend/app/blueprints/monitors.py` |
| Containers | `celery_worker/Dockerfile`, `backend/worker-entrypoint.sh`, compose `worker` + `beat` |

## Engines

All four are pure functions — no Flask, no database — returning a plain dict
with an `ok` flag. Network failures come back as `{"ok": false, "error": ...}`
rather than exceptions, so a probe never kills a worker.

| Engine | What it does | Scoring |
|---|---|---|
| `probe_http` | One GET, wall-clock latency, follows redirects | `is_up` = status < 400 |
| `inspect_ssl_certificate` | Real TLS handshake, decodes the DER leaf cert | valid = chain verified **and** unexpired **and** TLS ≥ 1.2 |
| `audit_security_headers` | 5 headers on a 100-point rubric | A+ to F; HSTS/CSP scored on *quality*, not presence |
| `audit_dns_posture` | SPF, DMARC, DNSSEC, CAA | SPF 30 + DMARC 40 + DNSSEC 15 + CAA 15 |

Two deliberate scoring choices:

- **A present header is not automatically a full score.** `Strict-Transport-Security`
  with a max-age under 180 days scores 60%, and a CSP containing `unsafe-inline`
  scores 50% — those configurations look compliant and protect nothing.
- **DMARC `p=none` scores 10 of 35.** It only sends reports; it does not stop
  spoofed mail. `p=reject` is the policy that does.

### DNS resolver configuration

The DNS engine queries `1.1.1.1 / 8.8.8.8 / 9.9.9.9` directly, overridable via
`DNS_RESOLVERS`. It cannot use `/etc/resolv.conf`: inside Docker that points at
the embedded resolver on `127.0.0.11`, which does not answer CAA or DNSKEY at
all and returns NoAnswer for apex TXT — every check would silently score zero.

## SSRF guard

Every engine resolves the hostname before connecting and rejects the target if
**any** resolved address is non-public — a hostname with one public and one
private A record is still an attack. Blocked: loopback, RFC1918, link-local
(which covers the `169.254.169.254` metadata endpoint), CGNAT `100.64.0.0/10`,
reserved, multicast, and IPv4-mapped IPv6 such as `::ffff:127.0.0.1`.

## Scheduling

Beat ticks every 30s and knows nothing about per-monitor intervals. The tick
runs `dispatch_due_pings`, which asks Postgres for monitors where
`last_checked_at + interval_seconds <= now()` and enqueues one task each. So
beat stays a fixed-cost tick regardless of monitor count, and one slow target
delays only its own probe.

`monitors.last_checked_at` exists for exactly this reason — the dispatcher never
has to aggregate over `ping_logs`. It is stamped even when a probe fails, so a
permanently broken target is not re-dispatched on every tick.

Two queues: `probes` (fast, high volume) and `scans` (slow, daily), so a backlog
of deep scans cannot delay uptime checks.

## New endpoints

| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/monitors/<id>/pings?hours=24&limit=100` | Recent pings + uptime %/avg latency |
| GET | `/api/v1/monitors/<id>/ssl` | Latest SSL scan |
| GET | `/api/v1/monitors/<id>/security` | Latest header + DNS audit |
| POST | `/api/v1/monitors/<id>/scan?kind=full\|ping` | Queue a scan now (202 + task id) |

## Deviations from the architecture doc

- **`org_id` added to all three result tables.** The doc lists only `monitor_id`.
  Denormalizing the tenant key lets `tenant_query` filter these high-volume
  tables without joining back to `monitors` on every dashboard read, and gives
  Phase 5 a key to partition on.
- **DNS results live in `security_audits.dns_payload`** rather than their own
  table — the doc specifies a DNS engine but no DNS table.
- **`security_audits.open_ports` stays null** until the Phase 4 port scanner.

## Not in this phase

Incident state machine, quorum/anti-flapping, and alert dispatch are Phase 3.
Right now a failed probe is recorded but nobody is notified.
