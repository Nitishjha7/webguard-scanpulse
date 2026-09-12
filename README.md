# WebGuard (ScanPulse)

Uptime monitoring and protocol-level security auditing for websites, in one
multi-tenant platform.

Most tools do one or the other. Uptime monitors tell you a site answered;
security scanners tell you its TLS is weak but not whether anyone could reach
it this morning. WebGuard runs both on the same targets, on the same schedule,
and reports them together — so "the checkout is up, but its certificate expires
in six days and Redis is open to the internet" is one screen, not three
products.

**Status:** all six phases complete — backend, worker fleet, and a React
dashboard, 307 backend tests passing. Everything below is built and running.

---

## What it actually checks

| Check | What it does | How it is scored |
|---|---|---|
| **Uptime** | One GET per interval, wall-clock latency, follows redirects | Up = HTTP < 400 |
| **TLS certificate** | Real handshake, decodes the DER leaf certificate | Valid = chain verifies **and** unexpired **and** TLS ≥ 1.2 |
| **HTTP security headers** | 5 headers on a 100-point rubric | A+ to F |
| **DNS posture** | SPF, DMARC, DNSSEC, CAA | A+ to F (SPF 30 + DMARC 40 + DNSSEC 15 + CAA 15) |
| **Exposed ports** | TCP connect scan over 19 ports that should not face the internet | 100 minus a penalty per finding |
| **Synthetic journeys** | Scripted multi-step flows in headless Chromium, screenshot on failure | Pass / fail per step |

Two scoring choices worth knowing about, because they change what the grades mean:

- **A present header is not automatically full marks.** `Strict-Transport-Security`
  with a max-age under 180 days scores 60%; a CSP containing `unsafe-inline`
  scores 50%. Those configurations look compliant and protect nothing.
- **DMARC `p=none` scores 10 of 35.** It sends reports; it does not stop spoofed
  mail. `p=reject` is the policy that does.

Likewise for ports: an open Redis or MongoDB is **critical** — they default to no
authentication at all — while SSH is only **medium**. SSH on a public host is
normal, and flagging it red would train people to ignore the whole report.

---

## Quick start

Everything runs in Docker. No local Python, Postgres or Node needed.

```bash
git clone git@github.com:Nitishjha7/webguard-scanpulse.git
cd webguard-scanpulse
cp .env.example .env          # edit SECRET_KEY / JWT_SECRET_KEY for anything real
docker compose up --build     # postgres, redis, api, celery worker, celery beat
```

First build takes a while — the worker image carries Chromium (~2.8 GB).

```bash
curl http://localhost:5000/health/ready
# {"database":"ok","status":"ok"}
```

The API container waits for Postgres, runs `flask db upgrade`, then starts
gunicorn on port 5000.

Open the dashboard at [http://localhost:3000](http://localhost:3000) — nginx
serves the built React app there and proxies `/api`, `/status` and `/health`
through to the backend, so the browser only ever talks to one origin.

### Seed a demo tenant

```bash
docker compose exec backend flask seed-demo
# Seeded org 'demo-org' with admin admin@webguard.local / changeme123
```

### Five minutes end to end

```bash
BASE=http://localhost:5000

# A tiny JSON field reader, borrowing the Python already in the container
# so nothing extra has to be installed on your machine.
field() { docker compose exec -T backend python -c "import sys,json;d=json.load(sys.stdin);[d:=d[k] for k in '$1'.split('.')];print(d)"; }

# 1. Log in
TOKEN=$(curl -s -X POST $BASE/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"admin@webguard.local","password":"changeme123"}' | field access_token)

# 2. Add a monitor
MONITOR=$(curl -s -X POST $BASE/api/v1/monitors \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"GitHub","url":"https://github.com","interval_seconds":300}' | field monitor.id)

# 3. Scan it now instead of waiting for the scheduler
curl -s -X POST "$BASE/api/v1/monitors/$MONITOR/scan?kind=full" \
  -H "Authorization: Bearer $TOKEN"

sleep 15

# 4. Read the results
curl -s "$BASE/api/v1/monitors/$MONITOR/ssl"      -H "Authorization: Bearer $TOKEN"
curl -s "$BASE/api/v1/monitors/$MONITOR/security" -H "Authorization: Bearer $TOKEN"
curl -s "$BASE/api/v1/monitors/$MONITOR/pings"    -H "Authorization: Bearer $TOKEN"
```

From here on the scheduler takes over: uptime probes on the monitor's own
interval, deep security scans once a day.

---

## How it fits together

```
   Browser ─────▶ ┌───────────────┐
                   │ frontend      │
                   │ nginx + React │
                   └───────┬───────┘
             ┌─────────────┼──────────────┐
             │  proxies /api, /status,     │
             │  /health straight through   │
             ▼                             │
      ┌──────────────┐                     │
      │  api (Flask) │◀────────────────────┘
      │  gunicorn    │
      └──────┬───────┘
             │
      ┌──────▼───────┐      ┌───────┐
      │ postgres 16  │      │ redis │
      │ partitioned  │      └───┬───┘
      └──────▲───────┘          │ broker
             │                  │
   ┌─────────┴──────────┐       │
   │ celery worker      │◀──────┤
   │ probes │ scans │   │       │
   │ synthetic (Chromium)│      │
   └────────────────────┘       │
                                │
      ┌──────────────┐          │
      │ celery beat  │──────────┘
      │ ticks 30s    │
      └──────────────┘
```

Six containers: `frontend`, `backend`, `worker`, `beat`, `postgres`, `redis`.

**Beat knows nothing about per-monitor intervals.** It ticks every 30 seconds
and asks Postgres which monitors are due
(`last_checked_at + interval_seconds <= now()`), then enqueues one task each.
So beat is a fixed-cost tick whether you have 10 monitors or 10,000, and one
slow target delays only its own probe.

**Three queues**, so slow work never blocks fast work:

| Queue | Carries |
|---|---|
| `probes` | Uptime checks — high volume, seconds |
| `scans` | TLS, headers, DNS, ports, maintenance — daily, slower |
| `synthetic` | Browser journeys — minutes, memory-hungry |

---

## Things that are easy to get wrong, and how they are handled

**One failed probe is not an outage.** A monitor must fail
`failure_threshold` consecutive probes (default 2, and the floor *is* 2) before
an incident opens and anyone is paged. Recovery is deliberately asymmetric:
opening needs a quorum, closing needs a single success — a site that is serving
traffic should not be shown as DOWN while it waits out a confirmation window.

**Users choose the URLs we fetch**, so every outbound probe resolves the
hostname first and refuses the target if **any** resolved address is non-public:
loopback, RFC1918, link-local (which covers the `169.254.169.254` cloud
metadata endpoint), CGNAT, and IPv4-mapped IPv6 like `::ffff:127.0.0.1`. A
hostname with one public and one private A record is still an attack. Synthetic
journeys get a second layer — a Playwright route handler aborts requests to
non-public hosts at runtime, because a page can redirect anywhere and
sub-resources never pass through our code.

**Two workers can probe the same monitor at once.** A partial unique index —
`UNIQUE (monitor_id) WHERE resolved_at IS NULL` — means the loser of that race
gets an integrity error instead of a duplicate incident and a duplicate page.

**A failing alert channel must not silence the others.** Each delivery is
independent, and every attempt is recorded on the incident as an audit trail. A
channel that fails 10 consecutive times is disabled rather than retried forever.

**`ping_logs` grows without bound**, so it is range-partitioned by month.
Retention is a `DROP TABLE`, not a `DELETE` walking hundreds of millions of
rows. Partitions are created *ahead* of time by a daily task — a probe that
failed because next month's partition was missing would look like a site
outage.

**Public status pages are the only unauthenticated pages here**, so what they
show is a deliberate list rather than whatever a model happens to carry.
Monitor URLs and latency are opt-in and off by default; incident `root_cause` is
never published at all, because our probes write it and it routinely names
internal infrastructure.

---

## API

All `/api/v1/*` endpoints need `Authorization: Bearer <access_token>` and are
scoped to the caller's organization. A resource belonging to another tenant
returns **404, not 403** — a 403 would confirm the id exists.

Roles: **Admin** (everything), **Engineer** (read + write, no deletes),
**Viewer** (read only).

### Auth

| Method | Path | Notes |
|---|---|---|
| POST | `/api/v1/auth/register` | Creates the organization and its first Admin |
| POST | `/api/v1/auth/login` | Returns access + refresh tokens |
| POST | `/api/v1/auth/refresh` | New access token (send the refresh token) |
| GET | `/api/v1/auth/me` | Current user and organization |
| POST | `/api/v1/auth/users` | Admin invites a member |

### Monitors

| Method | Path | Notes |
|---|---|---|
| GET / POST | `/api/v1/monitors` | List (paginated) / create |
| GET / PATCH / DELETE | `/api/v1/monitors/<id>` | Read / update / remove |
| POST | `/api/v1/monitors/<id>/scan?kind=full\|ping` | Queue a scan now |
| GET | `/api/v1/monitors/<id>/pings?hours=24` | Recent probes + uptime summary |
| GET | `/api/v1/monitors/<id>/uptime?days=30` | History from rollups (hourly ≤ 7d, else daily) |
| GET | `/api/v1/monitors/<id>/ssl` | Latest certificate scan |
| GET | `/api/v1/monitors/<id>/security` | Latest header + DNS + port audit |
| GET | `/api/v1/monitors/<id>/incidents` | Per-monitor history + downtime totals |

### Incidents

| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/incidents?state=open\|resolved&days=30` | Open incidents sorted first |
| GET | `/api/v1/incidents/summary` | Counts by status |
| GET | `/api/v1/incidents/<id>` | One incident, with delivery receipts |
| POST | `/api/v1/incidents/<id>/resolve` | Close by hand, with a note |

### Alert channels

| Method | Path | Notes |
|---|---|---|
| GET / POST | `/api/v1/channels` | List / create (`slack`, `discord`, `email`, `webhook`) |
| PATCH / DELETE | `/api/v1/channels/<id>` | Update / remove |
| POST | `/api/v1/channels/<id>/test` | Send a synthetic alert, inline |

### Synthetic journeys

| Method | Path | Notes |
|---|---|---|
| GET / POST | `/api/v1/synthetic` | List (with each check's latest run) / create |
| GET / PATCH / DELETE | `/api/v1/synthetic/<id>` | Read (secrets masked) / update / remove |
| POST | `/api/v1/synthetic/<id>/run` | Queue a run now |
| GET | `/api/v1/synthetic/<id>/runs` | History + success rate |
| GET | `/api/v1/synthetic/runs/<id>` | One run with per-step timings |
| GET | `/api/v1/synthetic/runs/<id>/screenshot` | Failure screenshot (PNG) |

### Status pages

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET / POST | `/api/v1/status-pages` | yes | List / create |
| GET / PATCH / DELETE | `/api/v1/status-pages/<id>` | yes | Manage |
| GET | `/api/v1/status-pages/<id>/preview` | yes | Payload, even while unpublished |
| GET | `/status/<slug>` | **no** | The public page |
| GET | `/status/<slug>.json` | **no** | Same data as JSON, CORS `*` |

### Health

| Method | Path | Notes |
|---|---|---|
| GET | `/health/live` | Process is up. Never touches the database |
| GET | `/health/ready` | Up **and** Postgres reachable |

---

## Writing a synthetic journey

A journey is a list of steps in a small JSON vocabulary — not JavaScript. A
`page.evaluate()` taking a tenant-authored string would be arbitrary code
execution inside the worker, so every action maps onto one specific, bounded
Playwright call.

```json
{
  "name": "Checkout flow",
  "interval_seconds": 900,
  "steps": [
    {"action": "goto",              "url": "https://shop.example.com/login"},
    {"action": "fill",              "selector": "#email",    "value": "bot@example.com"},
    {"action": "fill",              "selector": "#password", "value": "…", "secret": true},
    {"action": "click",             "selector": "button[type=submit]"},
    {"action": "wait_for_selector", "selector": ".dashboard"},
    {"action": "expect_text",       "selector": "h1", "value": "Dashboard"},
    {"action": "expect_url",        "value": "/dashboard"}
  ]
}
```

Actions: `goto`, `click`, `fill`, `select`, `press`, `wait_for_selector`,
`wait_for_timeout`, `expect_text`, `expect_not_text`, `expect_url`,
`expect_selector_count`, `screenshot`.

Steps marked `"secret": true` are stored so they can be replayed but masked on
every read. Editing a check you just read back will not overwrite the password
with asterisks.

Runs end in `PASSED`, `FAILED` (a step assertion broke — the customer's journey
is broken) or `ERROR` (the run could not complete — *we* are broken). The
distinction matters when you are reading a dashboard at 3am.

---

## Configuration

Everything is environment variables; see [`.env.example`](.env.example) for the
full list with comments.

| Variable | Default | What it controls |
|---|---|---|
| `SECRET_KEY`, `JWT_SECRET_KEY` | dev placeholder | **Change these.** |
| `DEFAULT_FAILURE_THRESHOLD` | `2` | Probes before an incident opens (floor is 2) |
| `MIN_INTERVAL_SECONDS` | `60` | Fastest allowed monitor interval |
| `MAX_MONITORS_PER_ORG` | `100` | Per-tenant quota |
| `SSL_EXPIRY_THRESHOLDS` | `30,14,7,3,1` | Days-left marks that raise an alert |
| `RAW_RETENTION_DAYS` | `7` | Before raw probes condense into hourly buckets |
| `HOURLY_RETENTION_DAYS` | `90` | Before hourly condense into daily |
| `DNS_RESOLVERS` | `1.1.1.1,8.8.8.8,9.9.9.9` | See the note below |
| `SENDGRID_API_KEY` | empty | Leave blank to disable email; other channels still work |

**Why DNS resolvers are explicit:** inside Docker, `/etc/resolv.conf` points at
the embedded resolver on `127.0.0.11`, which does not answer CAA or DNSKEY at
all and returns NoAnswer for apex TXT. Left to itself, every DNS posture check
would silently score zero.

---

## Development

```bash
# Tests — 307 of them, about 35 seconds
docker compose exec -e FLASK_ENV=testing backend pytest
docker compose exec -e FLASK_ENV=testing backend pytest --cov=app --cov-report=term-missing

# Logs
docker compose logs -f worker
docker compose logs -f beat

# Database
docker compose exec postgres psql -U webguard -d webguard

# Migrations
docker compose exec backend flask db migrate -m "what changed"
docker compose exec backend flask db upgrade

# Run a task by hand
docker compose exec worker python -c "
from celery_main import flask_app
from app.tasks.maintenance import refresh_rollups
with flask_app.app_context():
    print(refresh_rollups())
"
```

The suite runs against a real Postgres database, not SQLite — the schema uses
JSONB, a partial unique index, declarative partitioning and
`percentile_cont ... WITHIN GROUP`, none of which SQLite has. A SQLite run would
pass while the real thing broke. See [docs/testing.md](docs/testing.md).

Code lives on a bind mount, so edits are picked up by
`docker compose restart backend worker` without a rebuild. Rebuild only when
dependencies or a Dockerfile change.

---

## Project structure

```
backend/
  app/
    blueprints/     HTTP routes — auth, monitors, incidents, channels,
                    synthetic, status (public + admin)
    engines/        Pure probe functions: http, ssl, header, dns, port_scanner,
                    synthetic. No Flask, no database — testable in isolation
    models/         SQLAlchemy models
    services/       Logic spanning models: incidents (state machine), rollups,
                    partitions, status (the public disclosure boundary)
    notifications/  Alert formatting + Slack/Discord/email/webhook transports
    tasks/          Celery tasks and beat dispatchers
    utils/          SSRF guard, tenancy helpers, validators, error envelope
    templates/      The public status page
  migrations/       Alembic
  tests/            307 tests
celery_worker/      Worker image (Python + Chromium)
frontend/           React dashboard
  src/
    components/     Sidebar, TopBar, charts, tables — presentational pieces
    pages/          Dashboard, Login, per-section placeholders
    lib/            api.js (fetch client), dashboard.js (API -> view derivation)
  Dockerfile        Node build stage -> nginx runtime stage
  nginx.conf        Proxies /api, /status, /health to the backend
docs/               Architecture and per-phase notes
```

Engines are deliberately plain functions returning `{"ok": bool, ...}`. They
never raise for a network condition, so a badly behaved target cannot take down
a worker, and they can be called from a task, a test, or a shell without a
Flask context.

---

## Docs

- [Architecture & Technical Specification](docs/architecture.md) — the original design
- [Testing](docs/testing.md) — what is covered, what is not, and why
- [Phase 1](docs/phase-1.md) — scaffold, multi-tenancy, auth
- [Phase 2](docs/phase-2.md) — inspection engines, Celery workers
- [Phase 3](docs/phase-3.md) — incident state machine, quorum, alerting
- [Phase 4](docs/phase-4.md) — synthetic monitoring, port scanning
- [Phase 5](docs/phase-5.md) — partitioning, downsampling, status pages
- [Phase 6](docs/phase-6.md) — the dashboard, and how it was verified against live data

## Roadmap

| Phase | Status |
|---|---|
| 1. Core scaffold, multi-tenancy & models | **done** — [notes](docs/phase-1.md) |
| 2. Inspection engines & background workers | **done** — [notes](docs/phase-2.md) |
| 3. Quorum alerting & incident state machine | **done** — [notes](docs/phase-3.md) |
| 4. Synthetic E2E & port scanning | **done** — [notes](docs/phase-4.md) |
| 5. Time-series optimization & public status pages | **done** — [notes](docs/phase-5.md) |
| 6. React dashboard, visualization & deployment | **done** — [notes](docs/phase-6.md) |

### Known gaps

- **Dashboard covers the home page only.** `/monitors`, `/security`, `/synthetic`,
  `/alerts`, `/team`, `/settings` and the per-monitor detail page render an
  honest placeholder naming the endpoints that already work, rather than a fake
  "coming soon" — see `frontend/src/pages/Placeholder.jsx`.
- **Single region.** `ping_logs.region` and `incidents.regions` are modelled and
  populated, but every probe currently runs in one place, so only the
  iteration-count half of the quorum rule is active.
- **No Twilio SMS.** The four shipped channels cover the same need without a
  paid dependency.
- **No API rate limiting.** Redis is already in the stack for it; not wired up.

## Tech stack

Python 3.11 · Flask (app factory + blueprints) · Flask-JWT-Extended ·
SQLAlchemy 2 · Alembic · PostgreSQL 16 · Celery + Beat · Redis 7 ·
Playwright (headless Chromium) · cryptography · dnspython · Docker Compose ·
pytest · React 18 (Vite) · Tailwind CSS · Recharts · nginx
