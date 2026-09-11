# Phase 4 — Synthetic E2E & Port Scanning

Committed directly to `main` — single-developer workflow, no feature branches.

## What shipped

| Area | Files |
|---|---|
| Port scanner | `backend/app/engines/port_scanner.py` |
| Journey runner + step DSL | `backend/app/engines/synthetic.py` |
| Models | `backend/app/models/synthetic.py` (`synthetic_checks`, `synthetic_runs`) |
| Tasks | `backend/app/tasks/synthetic.py` |
| API | `backend/app/blueprints/synthetic.py` |
| Worker image | `celery_worker/Dockerfile` (Playwright + Chromium, 2.8GB) |

## Port scanner

An asyncio TCP connect scan over a fixed list of ports, folded into the daily
security audit. It fills `security_audits.open_ports`, which has been sitting
empty since Phase 2.

Deliberately **not** a general-purpose port scanner: the port list is fixed, the
host must be one the tenant already monitors, and the target goes through the
same SSRF guard as every other probe. The scan then connects to *the address
that was checked* rather than re-resolving the hostname, which closes the rebind
window between check and connect.

Severity is weighted by what an exposure actually means:

| Severity | Penalty | Examples |
|---|---|---|
| critical | 40 | Redis, MongoDB, Postgres, MySQL, Elasticsearch, Memcached, Docker API, Telnet |
| high | 20 | FTP, RDP, SMTP |
| medium | 5 | SSH, 8080, 8443, 9090 |
| info | 0 | 80, 443 |

**SSH is medium, not critical.** It is normal on a public host; flagging it red
would train users to ignore the whole report. Redis and Memcached are critical
because they default to no authentication at all.

A failed scan reports a **null** score rather than 100/A+, so "did not run"
cannot be misread as "nothing exposed".

Live results: `github.com` → 22/80/443, score 95. `example.com` (Cloudflare) →
80/443/8080/8443, score 90. `localhost` → blocked before any socket opened.

## Synthetic journeys

A check is a list of steps in a small JSON DSL:

```json
[
  {"action": "goto",              "url": "https://shop.example.com/login"},
  {"action": "fill",              "selector": "#email",    "value": "bot@example.com"},
  {"action": "fill",              "selector": "#password", "value": "…", "secret": true},
  {"action": "click",             "selector": "button[type=submit]"},
  {"action": "wait_for_selector", "selector": ".dashboard"},
  {"action": "expect_text",       "selector": "h1", "value": "Dashboard"}
]
```

Actions: `goto`, `click`, `fill`, `select`, `press`, `wait_for_selector`,
`wait_for_timeout`, `expect_text`, `expect_not_text`, `expect_url`,
`expect_selector_count`, `screenshot`.

**A closed vocabulary, not user-supplied JavaScript.** `page.evaluate()` with a
tenant-authored string would be arbitrary code execution inside the worker.
Every action here maps onto one specific, bounded Playwright call.

Validation runs in the API process at write time, so a malformed journey is
rejected while the user is looking at it rather than failing on a worker at 4am.

### Two layers of SSRF defence

1. **Definition time** — every `goto` URL is checked when the check is saved.
   Verified blocked: `169.254.169.254`, `127.0.0.1`, and `redis:6379` (a tenant
   cannot aim a journey at our own infrastructure either).
2. **Runtime** — a Playwright route handler aborts every request whose host is
   not publicly routable. Needed because a page can redirect anywhere, and
   sub-resources are fetched without passing through our code at all. Verified
   against a redirect to the metadata endpoint: the navigation never completed.

### Secrets

Login journeys carry real passwords. They must be stored to be replayed, but
`GET` masks any step marked `"secret": true`. A `PATCH` that echoes the mask
back keeps the stored value, so a read-edit-write round trip cannot overwrite a
password with asterisks — while a genuinely changed value is saved normally.

### Run status

| Status | Meaning |
|---|---|
| `PASSED` | Every step succeeded |
| `FAILED` | A step assertion did not hold — the customer's journey is broken |
| `ERROR` | The run could not complete: browser crash, bad config — *we* are broken |

The distinction matters: an ERROR is a loss of coverage, not evidence about the
customer's site. Both count toward the failure threshold, since either way the
journey is no longer being verified.

Failures follow the same anti-flapping rule as uptime monitors: one failed
journey is usually a slow page, not a broken checkout, so alerts wait for
`failure_threshold` (default 2) consecutive failures.

### Screenshots

Captured on failure to a Docker volume, not the database. The worker mounts it
read-write; the API mounts it **read-only** and serves images through
`GET /synthetic/runs/<id>/screenshot`, tenant-scoped like everything else.
Filenames are generated, never taken from user input, and the path is checked
for traversal anyway — serving an arbitrary path from a shared volume is a file
read primitive.

Old runs and their screenshots are pruned daily after 30 days. The rows are read
before deletion rather than bulk-deleted, because a bulk `DELETE` would orphan
every file on the volume.

## New endpoints

| Method | Path | Notes |
|---|---|---|
| GET/POST | `/api/v1/synthetic` | List (with each check's latest run) / create |
| GET/PATCH/DELETE | `/api/v1/synthetic/<id>` | Read (secrets masked) / update / remove |
| POST | `/api/v1/synthetic/<id>/run` | Queue a run now |
| GET | `/api/v1/synthetic/<id>/runs` | History + success rate and average duration |
| GET | `/api/v1/synthetic/runs/<id>` | One run with per-step timings |
| GET | `/api/v1/synthetic/runs/<id>/screenshot` | Failure screenshot (PNG) |

## Infrastructure notes

The worker image is now **2.8GB** — Chromium is most of it. That is why the
worker has always been a separate image from the API: the API never drives a
browser and stays at ~800MB.

Chromium's system libraries are installed by hand rather than via
`playwright install --with-deps`. The base is Debian bookworm, Playwright falls
back to its Ubuntu 20.04 package list, and `ttf-unifont` /
`ttf-ubuntu-font-family` do not exist in Debian — which aborts the entire
install, not just the fonts.

Browser runs get their own Celery queue (`synthetic`) alongside `probes` and
`scans`, with a longer time limit (180s soft / 240s hard) than an HTTP probe.

## Not in this phase

Multi-region execution — `ping_logs.region` and `incidents.regions` are modelled
and populated, but every probe still runs in one region.
