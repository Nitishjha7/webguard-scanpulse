import { useCallback, useEffect, useMemo, useState } from "react";
import HeroBanner from "../components/HeroBanner";
import LatestScan from "../components/LatestScan";
import MonitorsTable from "../components/MonitorsTable";
import RecentAlerts from "../components/RecentAlerts";
import SecurityDonut from "../components/SecurityDonut";
import StatCard from "../components/StatCard";
import UptimeOverview from "../components/UptimeOverview";
import { Activity, AlertTriangle, Clock, Shield, Wifi } from "../components/Icons";
import { api, settleAll } from "../lib/api";
import {
  buildAlerts,
  buildLatestScan,
  fleetAverageGrade,
  fleetUptimeSeries,
  gradeCounts,
  monitorState,
} from "../lib/dashboard";
import { ms, percent } from "../lib/format";

export default function Dashboard({ onAddMonitor, onToast }) {
  const [days, setDays] = useState(30);
  const [loading, setLoading] = useState(true);
  const [scanningId, setScanningId] = useState(null);
  const [data, setData] = useState({
    monitors: [],
    incidents: [],
    sslByMonitor: {},
    securityByMonitor: {},
    pingsByMonitor: {},
    uptimeSeries: [],
    syntheticByMonitor: {},
  });

  const load = useCallback(
    async (windowDays) => {
      setLoading(true);
      try {
        const [monitorsResponse, incidentsResponse, syntheticResponse] = await settleAll([
          api.monitors("?per_page=100"),
          api.incidents(`?days=${windowDays}`),
          api.synthetic(),
        ]);

        const monitors = monitorsResponse?.monitors || [];

        // Per-monitor detail in parallel. settleAll keeps one 404 — a monitor
        // that has never been scanned — from blanking the whole dashboard.
        const [ssl, security, pings, uptime] = await Promise.all([
          settleAll(monitors.map((m) => api.ssl(m.id))),
          settleAll(monitors.map((m) => api.security(m.id))),
          settleAll(monitors.map((m) => api.pings(m.id, 24))),
          settleAll(monitors.map((m) => api.uptime(m.id, windowDays))),
        ]);

        const byId = (values, key) =>
          Object.fromEntries(
            monitors.map((m, index) => [m.id, values[index]?.[key] ?? null]),
          );

        const synthetic = {};
        for (const check of syntheticResponse?.checks || []) {
          if (!check.monitor_id) continue;
          const bucket = synthetic[check.monitor_id] || { total: 0, passed: 0 };
          bucket.total += 1;
          if (check.latest_run?.passed) bucket.passed += 1;
          synthetic[check.monitor_id] = bucket;
        }

        setData({
          monitors,
          incidents: incidentsResponse?.incidents || [],
          sslByMonitor: byId(ssl, "ssl_scan"),
          securityByMonitor: byId(security, "security_audit"),
          pingsByMonitor: Object.fromEntries(
            monitors.map((m, index) => [m.id, pings[index]]),
          ),
          uptimeSeries: fleetUptimeSeries(uptime),
          syntheticByMonitor: synthetic,
        });
      } catch (error) {
        onToast?.({ tone: "error", message: error.message });
      } finally {
        setLoading(false);
      }
    },
    [onToast],
  );

  useEffect(() => {
    load(days);
  }, [days, load]);

  const view = useMemo(() => {
    const { monitors, incidents, securityByMonitor, pingsByMonitor } = data;

    const openByMonitor = {};
    for (const incident of incidents) {
      if (incident.is_open) openByMonitor[incident.monitor_id] = incident;
    }

    const rows = monitors.map((monitor) => {
      const pings = pingsByMonitor[monitor.id];
      const audit = securityByMonitor[monitor.id];
      return {
        id: monitor.id,
        name: monitor.name,
        url: monitor.url,
        uptime: pings?.summary?.uptime_percent ?? null,
        grade: audit?.grade || null,
        lastScan: audit?.scanned_at || monitor.last_scanned_at,
        state: monitorState(monitor, openByMonitor[monitor.id]),
      };
    });

    const up = rows.filter((r) => r.state === "up" || r.state === "degraded").length;
    const down = rows.filter((r) => r.state === "down").length;

    const latencies = Object.values(pingsByMonitor)
      .map((p) => p?.summary?.avg_latency_ms)
      .filter((v) => typeof v === "number");
    const avgLatency = latencies.length
      ? latencies.reduce((a, b) => a + b, 0) / latencies.length
      : null;

    const counts = gradeCounts(securityByMonitor);
    const strong = (counts["A+"] || 0) + (counts.A || 0);
    const graded = Object.values(counts).reduce((a, b) => a + b, 0);

    return {
      rows,
      total: monitors.length,
      up,
      down,
      avgLatency,
      counts,
      graded,
      strongShare: graded ? Math.round((strong / graded) * 100) : 0,
      averageGrade: fleetAverageGrade(securityByMonitor),
      alerts: buildAlerts({
        incidents,
        sslByMonitor: data.sslByMonitor,
        securityByMonitor,
        monitorsById: Object.fromEntries(monitors.map((m) => [m.id, m])),
      }),
      latestScan: buildLatestScan({
        monitors,
        securityByMonitor,
        sslByMonitor: data.sslByMonitor,
        pingsByMonitor,
        syntheticByMonitor: data.syntheticByMonitor,
      }),
      uptimeSpark: data.uptimeSeries.slice(-12).map((d) => d.uptime),
    };
  }, [data]);

  async function handleScan(monitor) {
    setScanningId(monitor.id);
    try {
      await api.scanMonitor(monitor.id, "full");
      onToast?.({ tone: "success", message: `Scan queued for ${monitor.name}.` });
      // The worker needs a moment; refresh once it has plausibly finished.
      setTimeout(() => load(days), 12000);
    } catch (error) {
      onToast?.({ tone: "error", message: error.message });
    } finally {
      setTimeout(() => setScanningId(null), 2000);
    }
  }

  async function handleDelete(monitor) {
    if (!window.confirm(`Delete "${monitor.name}"? Its history goes with it.`)) return;
    try {
      await api.deleteMonitor(monitor.id);
      onToast?.({ tone: "success", message: `${monitor.name} deleted.` });
      load(days);
    } catch (error) {
      onToast?.({ tone: "error", message: error.message });
    }
  }

  return (
    <div className="space-y-5 pb-8">
      <HeroBanner onAddMonitor={onAddMonitor} />

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-5">
        <StatCard
          label="Total Monitors"
          value={view.total}
          icon={Activity}
          tone="emerald"
          spark={view.uptimeSpark}
          caption={view.total ? "across this organization" : "add one to begin"}
        />
        <StatCard
          label="Sites Up"
          value={view.up}
          icon={Wifi}
          tone="blue"
          sparkType="bar"
          spark={view.uptimeSpark}
          caption={view.total ? `${percent((view.up / view.total) * 100)} of fleet` : "—"}
        />
        <StatCard
          label="Sites Down"
          value={view.down}
          icon={AlertTriangle}
          tone="red"
          sparkType="bar"
          spark={view.uptimeSpark.map((v) => 100 - v)}
          caption={view.total ? `${percent((view.down / view.total) * 100)} of fleet` : "—"}
        />
        <StatCard
          label="Avg. Security Grade"
          value={view.averageGrade}
          icon={Shield}
          tone="violet"
          sparkType="line"
          spark={view.uptimeSpark}
          caption={
            view.graded ? `${view.strongShare}% of sites A or higher` : "no scans yet"
          }
        />
        <StatCard
          label="Avg. Response Time"
          value={view.avgLatency === null ? "—" : ms(view.avgLatency)}
          icon={Clock}
          tone="amber"
          spark={view.uptimeSpark}
          caption="mean across all monitors, 24h"
        />
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)_minmax(0,0.95fr)]">
        <UptimeOverview
          series={data.uptimeSeries}
          days={days}
          onRangeChange={setDays}
          loading={loading}
        />
        <SecurityDonut counts={view.counts} total={view.total} loading={loading} />
        <RecentAlerts alerts={view.alerts} loading={loading} />
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.85fr)_minmax(0,1fr)]">
        <MonitorsTable
          rows={view.rows}
          loading={loading}
          onAddMonitor={onAddMonitor}
          onScan={handleScan}
          onDelete={handleDelete}
          scanningId={scanningId}
        />
        <LatestScan scan={view.latestScan} loading={loading} />
      </div>
    </div>
  );
}
