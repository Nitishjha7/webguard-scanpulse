# Testing

307 tests, ~78% line coverage across `backend/app`. The whole suite runs in about 35 seconds.

## Running them

```bash
docker compose exec -e FLASK_ENV=testing backend pytest
docker compose exec -e FLASK_ENV=testing backend pytest --cov=app --cov-report=term-missing
docker compose exec -e FLASK_ENV=testing backend pytest tests/test_incident_state_machine.py -v
```

Test dependencies live in `backend/requirements-dev.txt` and are installed by
the image unless it is built with `--build-arg INSTALL_DEV=false` — do that for
a production build.

## Why Postgres, not SQLite

The suite runs against a real `webguard_test` database on the same Postgres
container, created automatically on first run. SQLite would be faster and
simpler, and would also be a lie: the schema and several queries are
Postgres-specific.

- `JSONB` columns on `ssl_scans`, `security_audits`, `incidents`
- the **partial unique index** that stops two workers opening duplicate
  incidents — SQLite has no equivalent, so the concurrency guard would go
  untested
- `make_interval()` in the beat dispatcher, which computes a per-row deadline
- `extract('epoch', …)` in the incident summary
- **declarative range partitioning** on `ping_logs`, which has no SQLite
  equivalent at all
- `percentile_cont(...) WITHIN GROUP` and `ON CONFLICT DO UPDATE` in the
  downsampling upserts

A SQLite run would pass while the real thing broke.

Tables are emptied between tests rather than wrapped in a rolled-back
transaction. The incident state machine calls `session.rollback()` itself when
it loses an insert race; inside an enclosing test transaction that would
silently discard the test's own data and make a failure look like a pass.

The cleanup uses DELETE, not TRUNCATE. The tables hold a handful of rows each,
and TRUNCATE takes an ACCESS EXCLUSIVE lock and rewrites files — across the
whole suite that alone cost about three and a half minutes.

## What is covered

| File | Focus |
|---|---|
| `test_incident_state_machine.py` | Every transition, quorum, the partial-index race guard |
| `test_ssrf.py` | Loopback, RFC1918, metadata endpoint, CGNAT, IPv4-mapped IPv6, mixed resolution |
| `test_api_tenancy.py` | Registration, login, roles, cross-tenant access at the HTTP boundary |
| `test_notifications.py` | Host allowlist, failure isolation, channel disabling, secret masking |
| `test_engines.py` | Header and DNS scoring rubrics |
| `test_scheduling_and_alerts.py` | Beat dispatch windows, SSL expiry threshold crossing |
| `test_monitors_api.py` | Validation, quotas, duplicate URLs, ping/incident read endpoints |
| `test_probe_and_formatting.py` | Probe status classification, failure modes, alert message shape |
| `test_port_scanner.py` | SSRF refusal, port selection, severity weighting |
| `test_synthetic_dsl.py` | Step validation, the closed action vocabulary, secret masking |
| `test_synthetic_api.py` | Journey CRUD, the secret round-trip, failure-state folding |
| `test_partitions_and_rollups.py` | Partition routing, retention drops, idempotent downsampling |
| `test_status_pages.py` | What a public page must *not* disclose, and unknown-vs-operational |

Network calls are stubbed with `responses`, the socket layer is stubbed for the
port scanner, and DNS is patched where the SSRF guard would otherwise resolve a
test hostname for real. No test needs the internet, and no test launches a
browser.

## Regression tests

Four bugs found by hand during development each have a named test:

| Test | Bug |
|---|---|
| `test_down_monitor_that_returns_slowly_does_not_stay_down_forever` | A DOWN monitor that recovered but was slow hit the "incident already open" branch on every later probe and could never clear |
| `test_reserved_domain_account_can_still_log_in` | Login applied sign-up email rules, locking out the seeded demo admin at `@webguard.local` |
| `test_patching_onto_an_existing_url_is_a_conflict_not_a_500` | `POST /monitors` checked for duplicate URLs, `PATCH` did not, so the unique constraint surfaced as a 500 |
| `test_staying_between_marks_does_not_re_alert` | Guards the SSL countdown against re-alerting daily once a threshold is passed |

A fifth was found *by* the suite: `ssl_scans.days_left` declared the same index
name twice — once via `index=True` and once in `__table_args__` — which broke
`create_all()`. Alembic had deduplicated it, so it never surfaced in Docker.

## Known gaps

- `app/tasks/alerts.py` (29%) and `app/tasks/probes.py` — the Celery task
  wrappers. The logic inside them is tested through the plain functions they
  delegate to; the wrappers themselves need a Celery test harness.
- `app/engines/ssl_engine.py` (28%) — needs a TLS server fixture to exercise
  the handshake. Certificate *parsing* is what matters and is covered
  indirectly; the handshake itself is verified against live hosts by hand.
- `app/engines/dns_engine.py` (54%) — the scoring functions are fully covered;
  the resolver plumbing is not.
- `app/engines/synthetic.py` (40%) and `app/tasks/synthetic.py` (34%) — the
  step DSL, its validator and the failure-state folding are fully covered; the
  code that actually drives Chromium is not. Exercising it needs a browser and
  a controlled page, so it is verified by hand against real Chromium: a passing
  journey, an assertion failure, a selector timeout, and a page redirecting to
  the cloud metadata endpoint (blocked by the route handler).
