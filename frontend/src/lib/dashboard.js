/**
 * Turns the raw API responses into what the dashboard renders.
 *
 * Kept out of the components so the derivations — what counts as an alert, how
 * a fleet-wide uptime series is built from per-monitor rollups — are testable
 * and stated in one place rather than scattered through JSX.
 */
import { averageGrade, hostOf } from "./format";

/** A monitor is only "up" if it was probed recently and has no open incident. */
const FRESHNESS_MS = 30 * 60 * 1000;

export function monitorState(monitor, openIncident) {
  if (openIncident?.status === "DOWN") return "down";
  if (openIncident?.status === "DEGRADED") return "degraded";
  if (!monitor.last_checked_at) return "unknown";
  if (Date.now() - new Date(monitor.last_checked_at).getTime() > FRESHNESS_MS) {
    return "unknown";
  }
  return "up";
}

/**
 * Average per-monitor daily uptime into one fleet series.
 *
 * Averaging the daily percentages rather than summing raw checks means a
 * monitor on a 60-second interval does not drown out one on an hourly
 * interval — every site counts once per day, which is what "overall uptime
 * across all monitored sites" means to a reader.
 */
export function fleetUptimeSeries(uptimeResponses) {
  const byDate = new Map();

  for (const response of uptimeResponses) {
    for (const point of response?.series || []) {
      if (!point.checks) continue;
      const date = point.bucket.slice(0, 10);
      const entry = byDate.get(date) || { total: 0, count: 0 };
      entry.total += point.uptime_percent;
      entry.count += 1;
      byDate.set(date, entry);
    }
  }

  return [...byDate.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([date, { total, count }]) => ({
      date,
      uptime: Number((total / count).toFixed(3)),
    }));
}

export function gradeCounts(securityByMonitor) {
  const counts = {};
  for (const audit of Object.values(securityByMonitor)) {
    if (!audit?.grade) continue;
    counts[audit.grade] = (counts[audit.grade] || 0) + 1;
  }
  return counts;
}

export function fleetAverageGrade(securityByMonitor) {
  return averageGrade(
    Object.values(securityByMonitor)
      .map((audit) => audit?.grade)
      .filter(Boolean),
  );
}

/**
 * Build the alert feed.
 *
 * Alerts are derived rather than stored: the backend records incidents, scans
 * and audits, and "what should a human look at first" is a presentation
 * question. Sorted by severity then recency, because a critical finding from
 * this morning outranks an informational one from a minute ago.
 */
const SEVERITY_RANK = { critical: 0, high: 1, medium: 2, info: 3 };

export function buildAlerts({ incidents, sslByMonitor, securityByMonitor, monitorsById }) {
  const alerts = [];

  for (const incident of incidents) {
    const name = monitorsById[incident.monitor_id]?.name || incident.monitor_name || "Monitor";
    const host = hostOf(monitorsById[incident.monitor_id]?.url || "");
    if (incident.is_open) {
      alerts.push({
        id: `incident-${incident.id}`,
        severity: incident.status === "DOWN" ? "critical" : "high",
        title: incident.status === "DOWN" ? `${name} is down` : `${name} is degraded`,
        target: host || name,
        at: incident.started_at,
      });
    } else {
      alerts.push({
        id: `resolved-${incident.id}`,
        severity: "info",
        title: "Site back online",
        target: host || name,
        at: incident.resolved_at,
      });
    }
  }

  for (const [monitorId, scan] of Object.entries(sslByMonitor)) {
    if (!scan) continue;
    const host = hostOf(monitorsById[monitorId]?.url || "");

    if (scan.verify_error) {
      alerts.push({
        id: `ssl-invalid-${monitorId}`,
        severity: "critical",
        title: `TLS certificate is invalid`,
        target: host,
        at: scan.scanned_at,
      });
    } else if (scan.days_left !== null && scan.days_left <= 30) {
      alerts.push({
        id: `ssl-expiry-${monitorId}`,
        severity: scan.days_left <= 7 ? "critical" : "high",
        title: `TLS certificate expires in ${scan.days_left} days`,
        target: host,
        at: scan.scanned_at,
      });
    }
  }

  for (const [monitorId, audit] of Object.entries(securityByMonitor)) {
    if (!audit) continue;
    const host = hostOf(monitorsById[monitorId]?.url || "");

    for (const finding of audit.open_ports?.findings || []) {
      if (finding.severity !== "critical") continue;
      alerts.push({
        id: `port-${monitorId}-${finding.port}`,
        severity: "critical",
        title: `Open ${finding.service} port`,
        target: host,
        at: audit.scanned_at,
      });
    }

    const dmarc = audit.dns?.checks?.dmarc;
    if (dmarc && dmarc.policy !== "reject") {
      alerts.push({
        id: `dmarc-${monitorId}`,
        severity: "medium",
        title: dmarc.present
          ? `Weak DMARC (p=${dmarc.policy})`
          : "Missing DMARC (p=reject)",
        target: host,
        at: audit.scanned_at,
      });
    }
  }

  return alerts
    .sort((a, b) => {
      const rank = SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity];
      return rank !== 0 ? rank : new Date(b.at || 0) - new Date(a.at || 0);
    })
    .slice(0, 8);
}

/** The six-check summary for whichever monitor was scanned most recently. */
export function buildLatestScan({ monitors, securityByMonitor, sslByMonitor, pingsByMonitor, syntheticByMonitor }) {
  const scanned = monitors
    .filter((m) => securityByMonitor[m.id] || sslByMonitor[m.id])
    .sort((a, b) => {
      const at = (m) =>
        new Date(securityByMonitor[m.id]?.scanned_at || sslByMonitor[m.id]?.scanned_at || 0);
      return at(b) - at(a);
    })[0];

  if (!scanned) return null;

  const audit = securityByMonitor[scanned.id];
  const ssl = sslByMonitor[scanned.id];
  const pings = pingsByMonitor[scanned.id];
  const synthetic = syntheticByMonitor?.[scanned.id];

  const latestPing = pings?.pings?.[0];
  const ports = audit?.open_ports;
  const criticalPorts = (ports?.findings || []).filter((f) => f.severity === "critical");

  return {
    monitorId: scanned.id,
    host: hostOf(scanned.url),
    at: audit?.scanned_at || ssl?.scanned_at,
    checks: [
      {
        key: "uptime",
        label: "Uptime",
        ok: latestPing ? latestPing.is_up : null,
        value: latestPing
          ? latestPing.is_up
            ? `Up (${Math.round(latestPing.latency_ms)} ms)`
            : "Down"
          : "No probes yet",
      },
      {
        key: "ssl",
        label: "SSL / TLS",
        ok: ssl ? ssl.is_valid : null,
        value: ssl
          ? ssl.is_valid
            ? `Valid (${ssl.tls_version})`
            : ssl.verify_error || "Invalid"
          : "Not scanned",
      },
      {
        key: "headers",
        label: "Security Headers",
        ok: audit ? audit.grade !== "F" : null,
        value: audit ? `${audit.grade} (${audit.score}/100)` : "Not scanned",
      },
      {
        key: "dns",
        label: "DNS Posture",
        ok: audit?.dns_grade ? !["D", "F"].includes(audit.dns_grade) : null,
        value: audit?.dns_grade ? `${audit.dns_grade} (${audit.dns_score}/100)` : "Not scanned",
      },
      {
        key: "ports",
        label: "Exposed Ports",
        ok: ports?.ok ? criticalPorts.length === 0 : null,
        value: !ports?.ok
          ? "Not scanned"
          : criticalPorts.length
            ? `${criticalPorts.length} risky port${criticalPorts.length > 1 ? "s" : ""}`
            : "No risky ports",
      },
      {
        key: "synthetic",
        label: "Synthetic Tests",
        ok: synthetic ? synthetic.passed === synthetic.total : null,
        value: synthetic ? `${synthetic.passed} / ${synthetic.total} passed` : "None configured",
      },
    ],
  };
}
