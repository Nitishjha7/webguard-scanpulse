# Phase 1 — Core Scaffold, Multi-Tenancy & Models

Branch: `feature/phase-1-flask-scaffold`

Everything runs in Docker. No local Python or Postgres install is required.

## What shipped

| Area | Files |
|---|---|
| App factory | `backend/app/__init__.py`, `backend/wsgi.py` |
| Config (dev/test/prod) | `backend/app/config.py` |
| Extension singletons | `backend/app/extensions.py` |
| Models | `backend/app/models/{base,organization,user,monitor}.py` |
| Tenant isolation | `backend/app/utils/tenancy.py` |
| Error envelope + validators | `backend/app/utils/{errors,validators}.py` |
| Blueprints | `backend/app/blueprints/{auth,monitors,health}.py` |
| Migrations | `backend/migrations/` (Alembic via Flask-Migrate) |
| Containers | `backend/Dockerfile`, `backend/entrypoint.sh`, `docker-compose.yml` |

## Running it

```bash
cp .env.example .env          # edit SECRET_KEY / JWT_SECRET_KEY for anything real
docker compose up --build     # postgres + redis + backend
```

The backend entrypoint waits for Postgres, runs `flask db upgrade`, then starts
gunicorn on port 5000.

Generate the first migration once (only needed if `migrations/versions/` is empty):

```bash
docker compose exec backend flask db migrate -m "phase 1: orgs, users, monitors"
docker compose exec backend flask db upgrade
```

Seed a demo tenant:

```bash
docker compose exec backend flask seed-demo
```

## API surface

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/health/live` | — | Process liveness |
| GET | `/health/ready` | — | Liveness + Postgres reachable |
| POST | `/api/v1/auth/register` | — | Creates org + first Admin, returns tokens |
| POST | `/api/v1/auth/login` | — | Returns access + refresh tokens |
| POST | `/api/v1/auth/refresh` | refresh JWT | New access token |
| GET | `/api/v1/auth/me` | access JWT | Current user + org |
| POST | `/api/v1/auth/users` | Admin | Add member to own org |
| GET | `/api/v1/monitors` | any role | Paginated, org-scoped |
| POST | `/api/v1/monitors` | Admin, Engineer | Quota + duplicate-URL checked |
| GET | `/api/v1/monitors/<id>` | any role | 404 across tenants |
| PATCH | `/api/v1/monitors/<id>` | Admin, Engineer | Partial update |
| DELETE | `/api/v1/monitors/<id>` | Admin | 204 |

## How tenancy works

1. `@app.before_request` verifies the JWT (optional), loads the user row, and binds
   `g.current_user` / `g.org_id`.
2. Views call `tenant_query(Model)`, which always appends `WHERE org_id = :caller_org`.
3. `get_tenant_object_or_404` returns 404 — not 403 — for another tenant's row, so
   the API does not leak the existence of foreign records.

## Not in this phase (by design)

- SSRF resolution guards — they belong at connect time, with the probe engines (Phase 2).
  Phase 1 only validates URL *syntax*.
- Celery worker/beat — Redis is already running in compose, wiring lands in Phase 2.
- `ping_logs`, `ssl_scans`, `security_audits`, `incidents` tables — Phases 2 and 3.
