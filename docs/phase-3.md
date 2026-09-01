# Phase 3 — Quorum Alerting & Incident State Machine

Committed directly to `main` — single-developer workflow, no feature branches.

## What shipped

| Area | Files |
|---|---|
| Incident model | `backend/app/models/incident.py` |
| Channel model | `backend/app/models/notification.py` |
| State machine | `backend/app/services/incidents.py` |
| Alert formatting + transports | `backend/app/notifications/` |
| Alert tasks | `backend/app/tasks/alerts.py` |
| Incident & channel APIs | `backend/app/blueprints/{incidents,channels}.py` |

## The state machine

```
UP        --(N consecutive failures)-->  DOWN
UP        --(N consecutive slow)------>  DEGRADED
DEGRADED  --(N consecutive failures)-->  DOWN       (escalation)
DOWN      --(one success, slow)------->  DEGRADED   (de-escalation)
DOWN      --(one success, fast)------->  RESOLVED
DEGRADED  --(one success, fast)------->  RESOLVED
```

**Opening needs a quorum; closing needs one success.** That asymmetry is the
point. A single failed probe is a network blip, not an outage, so `N` defaults
to 2 (`DEFAULT_FAILURE_THRESHOLD`, floored at 2 — it cannot be configured down
to 1). Recovery is immediate because a target that is serving traffic should
not be shown as DOWN while it waits out a confirmation window.

Quorum state lives in counters on `monitors` (`consecutive_failures`,
`consecutive_degraded`) rather than being aggregated from `ping_logs`. The
state machine runs on **every** probe; scanning the highest-volume table each
time would make uptime checks scale with history length.

The state machine is evaluated inside the same transaction as the ping row, so
the two can never disagree. Notification is queued only *after* that commit — an
alert about an incident that got rolled back is worse than one that is a moment
late.

### One open incident per monitor, enforced by Postgres

```sql
CREATE UNIQUE INDEX uq_incidents_one_open_per_monitor
    ON incidents (monitor_id) WHERE resolved_at IS NULL;
```

Two workers probing the same monitor concurrently both see "no open incident"
and both try to insert. The partial index means the loser gets an
`IntegrityError` — caught and treated as "someone else opened it" — instead of a
duplicate incident and a duplicate page.

## Alert channels

| Type | Destination | Notes |
|---|---|---|
| `slack` | Incoming webhook | Attachment with colour-coded severity |
| `discord` | Webhook | Rich embed |
| `email` | SendGrid v3 | Plain-text + HTML |
| `webhook` | Any https endpoint | Raw JSON message |

One neutral message dict is built per event and each transport renders it — a
new channel type means a new renderer, not changes to the incident code.

**SendGrid is called over its REST API with `requests`, not the SDK.** It is one
POST; the SDK would pull a dependency tree into both the API and worker images
for no benefit.

### Security

- Webhook URLs are user-supplied and get fetched by a worker, so they go through
  the same SSRF guard as monitor targets — at creation time (so the user sees
  the error while they can still fix it) *and* at send time.
- `slack` and `discord` channels additionally must point at those services'
  hosts. A "Slack" channel aimed at an arbitrary host is a misconfiguration at
  best, and an exfiltration path at worst.
- Webhook URLs embed their own credential, so `to_dict()` never echoes one back
  in full.
- A channel that fails 10 consecutive times is disabled rather than retried
  forever; re-enabling it clears the strike count.

### Delivery guarantees

- One failing channel never blocks the others on the same incident — each
  delivery is independent and its outcome is recorded on the incident.
- Every delivery attempt is appended to `incidents.notifications` as an audit
  trail: channel, type, ok, detail, timestamp.
- `task_acks_late` means a worker killed mid-delivery re-runs the task, so a
  Redis `SET NX` marker dedupes. If Redis is unreachable the alert is sent
  anyway — a duplicate alert beats a silent outage.

## SSL expiry countdown

Thresholds default to `30,14,7,3,1` days. A mark fires only when it sits between
the previous scan's reading and this one, so crossing 30 does not then re-alert
every day until 14. No extra state is stored — the previous scan row *is* the
state. A certificate whose chain fails verification raises `ssl.invalid`
immediately instead of a countdown.

## New endpoints

| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/incidents?state=open\|resolved&days=30` | Open incidents sorted first |
| GET | `/api/v1/incidents/summary` | Counts by status — dashboard tiles |
| GET | `/api/v1/incidents/<id>` | One incident + its monitor |
| POST | `/api/v1/incidents/<id>/resolve` | Manual close with a note |
| GET | `/api/v1/monitors/<id>/incidents` | Per-monitor history + downtime totals |
| GET/POST | `/api/v1/channels` | List / create |
| PATCH/DELETE | `/api/v1/channels/<id>` | Update / remove |
| POST | `/api/v1/channels/<id>/test` | Send a synthetic alert, inline |

`POST /channels/<id>/test` runs inline rather than queued: the caller is waiting
to find out whether their webhook URL is correct, and a task id tells them
nothing.

Monitors gained `failure_threshold` and `degraded_latency_ms` on create/update.
Setting `degraded_latency_ms` to null disables degraded detection.

## Not in this phase

Twilio SMS is in the architecture's channel list but not implemented — the four
shipped transports cover the same alerting need, and SMS adds a paid dependency
and per-message cost that nothing here yet justifies. Multi-region quorum
(2 distinct regions rather than 2 iterations) is modelled — `ping_logs.region`
and `incidents.regions` both exist and are populated — but every probe currently
runs in one region, so only the iteration-count half of the rule is active.
