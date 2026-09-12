# Phase 5 — Time-Series Optimization & Public Status Pages

Committed directly to `main` — single-developer workflow, no feature branches.

## What shipped

| Area | Files |
|---|---|
| Partition management | `backend/app/services/partitions.py` |
| Downsampling | `backend/app/services/rollups.py`, `backend/app/models/rollups.py` |
| Maintenance tasks | `backend/app/tasks/maintenance.py` |
| Status page payload | `backend/app/services/status.py` |
| Status page model + API | `backend/app/models/status_page.py`, `backend/app/blueprints/status.py` |
| Public template | `backend/app/templates/status.html` |
| Migration | `backend/migrations/versions/a4c71e9b02d5_*.py` (hand-written) |

## Partitioning `ping_logs`

`ping_logs` is the only table in the system that grows without bound — one row
per monitor per interval, forever. It is now **range partitioned by month** on
`checked_at`.

Two things this buys:

- **Retention is a `DROP TABLE`**, not a `DELETE` that has to walk and then
  vacuum hundreds of millions of rows.
- **Dashboard queries touch one partition.** "The last 24 hours" no longer
  scans an index spanning the whole history.

Postgres requires the partition key to appear in every unique constraint, so
the primary key changed from `id` to `(id, checked_at)`.

The migration is hand-written — Alembic does not model declarative
partitioning, and a plain table cannot *become* a partitioned one. It renames
the original, builds the partitioned parent alongside it, copies the rows,
moves the identity sequence past the copied ids, and drops the original.
Verified on the live database: 262 rows in, 262 rows out, all routed to the
correct month.

### Partitions are created ahead of time

A daily beat task keeps three months of headroom. Never lazily on insert: a
probe that failed because tomorrow's partition did not exist yet would be
recorded as **a site outage** — the system would invent an incident out of its
own housekeeping.

There is also a `DEFAULT` partition as a safety net, because losing a
measurement is worse than an untidy table. It comes with a catch worth stating:
Postgres refuses to create a partition whose range overlaps rows already sitting
in the default. So the maintenance task counts those rows and logs an **error**
if any exist — otherwise partition creation would start failing silently months
later.

Retention drops whole months only. A partition goes when *every* row in it is
older than the cutoff, which means the effective raw window is longer than the
nominal one. The alternative is deleting rows we promised to keep.

## Downsampling

| Grain | Source | Kept for |
|---|---|---|
| Raw probes | — | `RAW_RETENTION_DAYS` (7) |
| `ping_rollups_hourly` | raw probes | `HOURLY_RETENTION_DAYS` (90) |
| `ping_rollups_daily` | hourly buckets | indefinitely |

**Deviation from the architecture doc:** the doc specifies TimescaleDB
continuous aggregates. This uses ordinary tables refreshed by a beat task
instead. The reasons: it keeps the stack on stock Postgres 16, it works on any
managed instance (RDS, Cloud SQL, Neon) without an extension that needs
`shared_preload_libraries`, and the aggregation logic is plain SQL that the test
suite can assert on. TimescaleDB would have given incremental refresh for free;
this pays for that with a 15-minute beat task instead.

Every refresh is an **idempotent upsert** over a bounded window, keyed on
`(monitor_id, bucket)`. That single property is what makes the maintenance task
safe to retry, safe to run twice, and safe to run after a gap — a re-run
recomputes the same numbers and writes them again. It also means a probe written
a moment after its bucket was rolled up gets picked up by the next pass, which
is why every run re-processes a two-hour overlap.

`run_retention` **condenses before it deletes**, always. History lost to a
retention job is not recoverable, so the backfill runs first and the partition
drop second.

### What the rollups store, and why

- **Percentiles, not just the mean.** An average hides the slow tail users
  actually feel, and once the raw rows are gone it cannot be recovered.
- **Daily latency is weighted by each hour's check count**, so a quiet hour does
  not count as much as a busy one. Uptime stays exact because it is summed from
  counts; the latency mean is approximate by construction — acceptable for a
  90-day-old number, and stated in the code rather than quietly assumed.
- **Daily p95 is the worst hour's p95.** A true daily percentile is not
  recoverable from bucketed data, and the worst hour is the number an operator
  actually wants.

Live result on the demo tenant: 36 hourly buckets and 8 daily buckets rolled up
from real probe history.

## Public status pages

The only unauthenticated pages in the system. `app/services/status.py` builds
the entire payload and **is the disclosure boundary** — the template only
formats what it is handed, so nothing that is not built there can leak, even if
a template is edited carelessly later.

| Disclosure | Default |
|---|---|
| Monitor names | shown |
| Monitor URLs | **hidden** — opt-in via `show_urls` |
| Latency | **hidden** — opt-in via `show_latency` |
| Incidents | shown |
| Incident root cause | **never published** |

Root cause is written by our own probes and routinely carries internal
detail — resolved IP addresses, DNS errors naming infrastructure. It has no
place on a page the whole internet can read.

A monitor with no probe in the last 30 minutes reports **`unknown`**, not
`operational`. Saying a service is up without a recent measurement is a claim we
cannot support.

Other choices:

- **An unpublished page and a nonexistent one return byte-identical 404s.**
  Whether a slug exists is not something an anonymous visitor gets to learn.
- **No third-party assets** — no CDN, no web fonts, no analytics. One fewer
  party who learns that someone is checking on an outage.
- **`Content-Security-Policy: default-src 'none'`.** The page runs no scripts
  and loads nothing remote, so a stored XSS in a tenant-supplied field would
  have nothing to execute with.
- **30-second cache header.** Short enough that a new outage appears quickly,
  long enough to survive the traffic spike an outage itself produces — which is
  exactly when this page has to stay up.
- Custom domains are matched on the `Host` header at the application root, so a
  tenant CNAME serves their page while our own hostname keeps the API banner.

## New endpoints

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/status/<slug>` | — | Rendered HTML page |
| GET | `/status/<slug>.json` | — | Same data as JSON, `Access-Control-Allow-Origin: *` |
| GET | `/` on a custom domain | — | That tenant's status page |
| GET/POST | `/api/v1/status-pages` | JWT | List / create |
| GET/PATCH/DELETE | `/api/v1/status-pages/<id>` | JWT | Manage |
| GET | `/api/v1/status-pages/<id>/preview` | JWT | Payload preview, works while unpublished |
| GET | `/api/v1/monitors/<id>/uptime?days=30` | JWT | Rollup-backed history; hourly grain ≤ 7 days, daily beyond |

## Not in this phase

Multi-region execution — `ping_logs.region` and `incidents.regions` are modelled
and populated, but every probe still runs in one region.
