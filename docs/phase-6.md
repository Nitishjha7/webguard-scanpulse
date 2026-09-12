# Phase 6 — React Dashboard, Visualization & Dockerization

Committed directly to `main` — single-developer workflow, no feature branches.

## What shipped

| Area | Files |
|---|---|
| Dashboard shell | `frontend/src/App.jsx`, `Sidebar.jsx`, `TopBar.jsx` |
| Dashboard page | `frontend/src/pages/Dashboard.jsx`, `frontend/src/lib/dashboard.js` |
| Login / register | `frontend/src/pages/Login.jsx` |
| Charts | `UptimeOverview.jsx` (area), `SecurityDonut.jsx` (donut), `StatCard.jsx` (sparklines) — all Recharts |
| Data + tables | `MonitorsTable.jsx`, `LatestScan.jsx`, `RecentAlerts.jsx` |
| API client | `frontend/src/lib/api.js` |
| Container | `frontend/Dockerfile` (Node build → nginx runtime), `frontend/nginx.conf` |

## Architecture

Two-stage Docker build: Node compiles the Vite bundle, then an nginx-alpine
image serves the static output. The runtime image carries no Node, no source,
no `node_modules` — only compiled assets and a web server, so it stays small
and has nothing to exploit at runtime.

nginx also reverse-proxies `/api/*`, `/status/*` and `/health/*` to the
`backend` container. That keeps the browser on one origin, so there is no CORS
preflight and no separate API base URL to configure per environment — the
dashboard talks to `/api/v1/...` and nginx decides where that actually goes.

In dev, Vite's own proxy (`vite.config.js`) does the same job so `npm run dev`
behaves identically to the containerized build.

## Where the data comes from

**Nothing on the dashboard is mocked.** Every number is derived at render time
from the same endpoints documented in the [README](../README.md#api):

- Stat cards, the uptime chart, the security donut and the monitors table all
  come from `GET /monitors`, `/pings`, `/uptime`, `/ssl` and `/security` — one
  request per monitor per check, fanned out in parallel and reduced client-side
  in `lib/dashboard.js`.
- **Recent Alerts is derived, not stored.** The backend records incidents,
  scans and audits; deciding "what should a human look at first" is a
  presentation question, so it lives in `buildAlerts()` — sorted by severity,
  then recency, capped at 8.
- **Latest Scan** picks whichever monitor was scanned most recently and lays
  out its six checks (uptime, TLS, headers, DNS, ports, synthetic) with a
  pass/fail/unknown state per row, mirroring the API's own audit shape.

A single failed detail request (a monitor that has never been scanned, say)
does not blank the page — `settleAll()` resolves every promise, fulfilled or
rejected, and a missing result just shows as "Not scanned" rather than an
error boundary.

## Design decisions

**No global state library.** The dashboard is one page deep for now — a
`session` object in `App.jsx`, and each page fetching what it needs. Redux or
a query cache would be solving a problem this app does not have yet.

**No icon package.** `components/Icons.jsx` hand-rolls the ~30 glyphs the UI
needs as inline SVG. A library would cost more bundle weight than every icon
in the app combined.

**Chart chunk isolated.** Recharts (and its d3 dependencies) is most of the
JS bundle and changes far less often than app code, so `vite.config.js` splits
it into its own `charts` chunk — a code change ships without re-downloading
the charting library.

**Stat card labels wrap, they don't truncate.** The number sits on its own row
below the icon and label, so a two-line label like "Avg. Security Grade" costs
nothing. An earlier version put the number beside the label, which squeezed
both into "Avg. Securi…" style truncation under the sparkline's width.

**The lighthouse artwork is optional.** `HeroBanner` and `Login` try to load
`/assets/hero-lighthouse.jpg` / `/assets/lighthouse-portrait.jpg` and fall back
to a hand-drawn SVG scene (`LighthouseScene.jsx`) on a 404. Dropping a real
photo in later requires no code change.

## Verified against a live stack

Screenshotted through Playwright (the same engine the backend's synthetic
checks use) against the real `docker compose` stack, logged in as the seeded
demo tenant with seven real monitors — GitHub, Wikipedia, Cloudflare,
Python.org, Mozilla, Example, and a deliberately-expired TLS host — scanned in
full:

- Stat cards, donut and alert feed matched the database exactly (7 monitors,
  6 up / 1 down, grade mix A×3/D×1/F×3).
- The monitors table correctly showed the expired-certificate monitor as
  **Down** in red while the other six showed **Up**.
- Recent Alerts surfaced the real findings: the expired certificate, and
  DMARC policies weaker than `p=reject` on two of the domains.
- Latest Scan rendered the six-check breakdown for whichever monitor was
  scanned most recently, including "certificate has expired" read verbatim
  from the SSL engine's `verify_error`.

One real layout bug was caught this way and fixed before commit: the
monitors table (874px) was wider than its card (780px), silently clipping the
row-action menu behind an internal horizontal scrollbar that a full-page
screenshot does not reveal by scrolling. Tightened column padding and widened
the table's share of the page grid until the table fit without scrolling.

## Not in this phase

Only the dashboard's home page is built. `/monitors`, `/security`,
`/synthetic`, `/alerts`, `/team`, `/settings` and the per-monitor detail page
render an honest placeholder naming the API endpoints that already work,
rather than a fake "coming soon" — see `pages/Placeholder.jsx`. Building those
out is straightforward from here: the API client and data-shaping patterns in
`lib/dashboard.js` already cover what each of them needs.
