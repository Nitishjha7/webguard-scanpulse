import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { GitHub, Globe, MoreHorizontal, Plus, Refresh, Server, Store } from "./Icons";
import { gradeBadge, hostOf, percent, relativeTime, uptimeColor } from "../lib/format";

/** Pick a recognisable glyph from the hostname. Cosmetic only. */
function iconFor(url) {
  const host = hostOf(url).toLowerCase();
  if (host.includes("github")) return GitHub;
  if (host.includes("shop") || host.includes("store")) return Store;
  if (host.startsWith("api.") || host.includes("api")) return Server;
  return Globe;
}

function RowMenu({ monitor, onScan, onDelete }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    if (!open) return;
    const close = (event) => {
      if (!ref.current?.contains(event.target)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  return (
    <div ref={ref} className="relative flex justify-end">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label={`Actions for ${monitor.name}`}
        className="grid h-8 w-8 place-items-center rounded-lg text-slate-400 hover:bg-slate-100 hover:text-slate-600"
      >
        <MoreHorizontal size={18} />
      </button>
      {open && (
        <div className="absolute right-0 top-9 z-20 w-44 overflow-hidden rounded-lg border border-slate-200 bg-white py-1 shadow-lift">
          <Link
            to={`/monitors/${monitor.id}`}
            className="block px-3.5 py-2 text-xs font-medium text-slate-700 hover:bg-slate-50"
          >
            View details
          </Link>
          <button
            type="button"
            onClick={() => {
              onScan(monitor);
              setOpen(false);
            }}
            className="block w-full px-3.5 py-2 text-left text-xs font-medium text-slate-700 hover:bg-slate-50"
          >
            Scan now
          </button>
          <button
            type="button"
            onClick={() => {
              onDelete(monitor);
              setOpen(false);
            }}
            className="block w-full px-3.5 py-2 text-left text-xs font-medium text-red-600 hover:bg-red-50"
          >
            Delete monitor
          </button>
        </div>
      )}
    </div>
  );
}

export default function MonitorsTable({
  rows,
  loading,
  onAddMonitor,
  onScan,
  onDelete,
  scanningId,
}) {
  return (
    <section className="card overflow-hidden">
      <div className="flex items-center justify-between gap-3 px-5 pt-5 pb-4">
        <h2 className="text-[15px] font-bold text-slate-900">Monitored Sites</h2>
        <button type="button" onClick={onAddMonitor} className="btn-primary !px-3.5 !py-2 text-xs">
          <Plus size={15} />
          Add Monitor
        </button>
      </div>

      {loading ? (
        <div className="space-y-2 px-5 pb-5">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="h-12 animate-pulse rounded-lg bg-slate-100" />
          ))}
        </div>
      ) : rows.length === 0 ? (
        <div className="px-5 pb-10 pt-6 text-center">
          <span className="mx-auto grid h-12 w-12 place-items-center rounded-full bg-slate-100 text-slate-400">
            <Globe size={24} />
          </span>
          <p className="mt-3 text-sm font-semibold text-slate-700">No monitors yet</p>
          <p className="mx-auto mt-1 max-w-xs text-xs text-slate-500">
            Add a URL and WebGuard starts checking uptime, TLS, headers, DNS and
            exposed ports on a schedule.
          </p>
          <button type="button" onClick={onAddMonitor} className="btn-primary mx-auto mt-4">
            <Plus size={16} />
            Add your first monitor
          </button>
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[660px] text-sm">
            <thead>
              <tr className="border-y border-slate-100 bg-slate-50/60 text-left text-[11px] uppercase tracking-wide text-slate-500">
                <th className="py-2.5 pl-4 pr-2 font-semibold">Name</th>
                <th className="px-2 py-2.5 font-semibold">URL</th>
                <th className="px-2 py-2.5 font-semibold">Uptime (24h)</th>
                <th className="px-2 py-2.5 font-semibold">Security Grade</th>
                <th className="px-2 py-2.5 font-semibold">Last Scan</th>
                <th className="px-2 py-2.5 font-semibold">Status</th>
                <th className="py-2.5 pl-2 pr-4 text-right font-semibold">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {rows.map((row) => {
                const Icon = iconFor(row.url);
                const isDown = row.state === "down";
                const isUnknown = row.state === "unknown";
                return (
                  <tr key={row.id} className="hover:bg-slate-50/70">
                    <td className="py-3 pl-4 pr-2">
                      <Link
                        to={`/monitors/${row.id}`}
                        className="flex items-center gap-2.5 font-semibold text-slate-800 hover:text-brand-600"
                      >
                        <span className="grid h-7 w-7 shrink-0 place-items-center rounded-md bg-slate-100 text-slate-600">
                          <Icon size={15} />
                        </span>
                        <span className="truncate">{row.name}</span>
                      </Link>
                    </td>

                    <td className="max-w-[150px] px-2 py-3">
                      <a
                        href={row.url}
                        target="_blank"
                        rel="noreferrer noopener"
                        className="block truncate text-brand-600 hover:underline"
                      >
                        {row.url}
                      </a>
                    </td>

                    <td className="px-2 py-3">
                      <div className="flex items-center gap-2">
                        <span className="w-[46px] shrink-0 tabular-nums text-slate-700">
                          {percent(row.uptime)}
                        </span>
                        <span className="h-1.5 w-16 overflow-hidden rounded-full bg-slate-200">
                          <span
                            className={`block h-full rounded-full ${uptimeColor(row.uptime)}`}
                            style={{ width: `${Math.max(2, row.uptime ?? 0)}%` }}
                          />
                        </span>
                      </div>
                    </td>

                    <td className="px-2 py-3">
                      <span
                        className={`inline-block rounded-md px-2 py-0.5 text-xs font-bold ring-1 ring-inset ${gradeBadge(
                          row.grade,
                        )}`}
                      >
                        {row.grade || "—"}
                      </span>
                    </td>

                    <td className="whitespace-nowrap px-2 py-3 text-slate-500">
                      {relativeTime(row.lastScan)}
                    </td>

                    <td className="px-2 py-3">
                      <span
                        className={`inline-flex items-center gap-1.5 text-xs font-semibold ${
                          isDown
                            ? "text-red-600"
                            : isUnknown
                              ? "text-slate-400"
                              : "text-emerald-600"
                        }`}
                      >
                        <span
                          className={`h-1.5 w-1.5 rounded-full ${
                            isDown
                              ? "bg-red-500"
                              : isUnknown
                                ? "bg-slate-300"
                                : "bg-emerald-500"
                          }`}
                        />
                        {isDown ? "Down" : isUnknown ? "Unknown" : "Up"}
                      </span>
                    </td>

                    <td className="py-3 pl-2 pr-4">
                      {scanningId === row.id ? (
                        <span className="flex items-center justify-end gap-1.5 text-xs text-brand-600">
                          <Refresh size={14} className="animate-spin" />
                          Queued
                        </span>
                      ) : (
                        <RowMenu monitor={row} onScan={onScan} onDelete={onDelete} />
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
