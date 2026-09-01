# WebGuard (ScanPulse)

**Website Health, SSL Certificate Inspection, DNS Threat Posture & Synthetic Monitoring Platform**

WebGuard (ScanPulse) is an enterprise-grade, distributed website observability and security compliance platform. It bridges conventional uptime monitoring (uptime/latency pings) with deep protocol-level security evaluation (SSL/TLS, DNS SPF/DKIM/DMARC, HTTP security headers, open ports, and synthetic E2E browser checks).

## Tech Stack

- **Backend:** Python 3.11+, Flask (App Factory + Blueprints), Flask-JWT-Extended
- **Async Tasks:** Celery + Celery Beat, Redis 7.x (broker/cache)
- **Database:** PostgreSQL 16 / TimescaleDB
- **Inspection Engines:** cryptography, dnspython, socket/ssl, requests
- **Synthetic Monitoring:** Playwright (headless Chromium)
- **Frontend:** React (Vite), Tailwind CSS, Chart.js / Recharts
- **Notifications:** SendGrid, Twilio, Slack & Discord webhooks

## Project Structure

```
backend/app/blueprints/   # API route blueprints
backend/app/engines/      # SSL, DNS, header & port inspection engines
backend/app/models/       # SQLAlchemy models
backend/migrations/       # Alembic migrations
celery_worker/            # Celery worker + beat scheduler
frontend/src/             # React dashboard
docs/                     # Architecture & setup docs
```

## Quick Start (Docker)

```bash
cp .env.example .env
docker compose up --build
curl http://localhost:5000/health/ready
```

Everything runs in containers (Postgres 16, Redis 7, Flask API) - no local Python needed.
Seed a demo tenant with `docker compose exec backend flask seed-demo`.

## Docs

- [Architecture & Technical Specification](docs/architecture.md)
- [Setup Steps](docs/setup.md)
- [Phase 1 Notes & API Reference](docs/phase-1.md)
- [Phase 2 Notes: Engines & Workers](docs/phase-2.md)

## Roadmap

Development is split into 6 phases — see [docs/architecture.md](docs/architecture.md#5-phase-wise-implementation-roadmap--milestones) for full details:

1. Core Scaffold, Multi-Tenancy & Models — **done** ([notes](docs/phase-1.md))
2. Inspection Engines & Background Workers — **done** ([notes](docs/phase-2.md))
3. Quorum Alerting & Incident State Machine
4. Synthetic E2E & Port Scanning
5. Time-Series Optimization & Public Status Pages
6. React Dashboard, Visualization & Dockerization
