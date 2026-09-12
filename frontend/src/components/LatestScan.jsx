import { Link } from "react-router-dom";
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  Beaker,
  CheckCircle,
  Globe,
  Lock,
  Server,
  Shield,
} from "./Icons";
import { relativeTime } from "../lib/format";

const CHECK_ICONS = {
  uptime: Activity,
  ssl: Lock,
  headers: Shield,
  dns: Globe,
  ports: Server,
  synthetic: Beaker,
};

/**
 * The six checks for one monitor, in one column.
 *
 * Each row states the verdict in words rather than only a colour — "Valid (TLS
 * 1.3)" tells you what passed, where a green tick alone does not.
 */
export default function LatestScan({ scan, loading }) {
  if (loading) {
    return (
      <section className="card card-pad">
        <div className="h-5 w-40 animate-pulse rounded bg-slate-100" />
        <div className="mt-5 space-y-3">
          {[0, 1, 2, 3, 4, 5].map((i) => (
            <div key={i} className="h-8 animate-pulse rounded-lg bg-slate-100" />
          ))}
        </div>
      </section>
    );
  }

  if (!scan) {
    return (
      <section className="card card-pad flex h-full flex-col items-center justify-center text-center">
        <span className="grid h-11 w-11 place-items-center rounded-full bg-slate-100 text-slate-400">
          <Shield size={22} />
        </span>
        <p className="mt-3 text-sm font-semibold text-slate-700">No scans yet</p>
        <p className="mt-1 max-w-[220px] text-xs text-slate-500">
          Add a monitor and run a scan to see its full security posture here.
        </p>
      </section>
    );
  }

  return (
    <section className="card flex h-full flex-col">
      <div className="flex items-baseline justify-between gap-3 px-5 pt-5">
        <h2 className="min-w-0 truncate text-[15px] font-bold text-slate-900">
          Latest Scan: <span className="text-slate-700">{scan.host}</span>
        </h2>
        <span className="shrink-0 text-xs text-slate-500">{relativeTime(scan.at)}</span>
      </div>

      <ul className="flex-1 space-y-0.5 px-3 py-3">
        {scan.checks.map((check) => {
          const Icon = CHECK_ICONS[check.key] || Shield;
          return (
            <li
              key={check.key}
              className="flex items-center gap-2.5 rounded-lg px-2 py-2 hover:bg-slate-50"
            >
              <span className="grid h-7 w-7 shrink-0 place-items-center rounded-md bg-slate-100 text-slate-500">
                <Icon size={15} />
              </span>
              <span className="text-[13px] font-medium text-slate-700">{check.label}</span>
              <span
                className={`ml-auto flex items-center gap-1.5 text-right text-[13px] font-medium ${
                  check.ok === false
                    ? "text-red-600"
                    : check.ok === null
                      ? "text-slate-400"
                      : "text-slate-600"
                }`}
              >
                {check.ok === false ? (
                  <AlertTriangle size={15} className="shrink-0 text-red-500" />
                ) : check.ok === null ? (
                  <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-slate-300" />
                ) : (
                  <CheckCircle size={15} className="shrink-0 text-emerald-500" />
                )}
                {check.value}
              </span>
            </li>
          );
        })}
      </ul>

      <div className="px-5 pb-5">
        <Link to={`/monitors/${scan.monitorId}`} className="btn-dark">
          View Full Report
          <ArrowRight size={15} />
        </Link>
      </div>
    </section>
  );
}
