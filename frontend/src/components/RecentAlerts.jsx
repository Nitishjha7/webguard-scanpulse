import { Link } from "react-router-dom";
import { AlertOctagon, AlertTriangle, ArrowRight, CheckCircle, Info } from "./Icons";
import { relativeTime } from "../lib/format";

const SEVERITY = {
  critical: {
    icon: AlertOctagon,
    wrap: "bg-red-50 text-red-600",
    badge: "bg-red-100 text-red-700",
    label: "Critical",
  },
  high: {
    icon: AlertTriangle,
    wrap: "bg-amber-50 text-amber-600",
    badge: "bg-amber-100 text-amber-700",
    label: "High",
  },
  medium: {
    icon: Info,
    wrap: "bg-blue-50 text-blue-600",
    badge: "bg-blue-100 text-blue-700",
    label: "Medium",
  },
  info: {
    icon: CheckCircle,
    wrap: "bg-emerald-50 text-emerald-600",
    badge: "bg-emerald-100 text-emerald-700",
    label: "Info",
  },
};

export default function RecentAlerts({ alerts, loading }) {
  return (
    <section className="card flex h-full flex-col">
      <div className="flex items-center justify-between px-5 pt-5">
        <h2 className="text-[15px] font-bold text-slate-900">Recent Alerts</h2>
        <Link
          to="/alerts"
          className="inline-flex items-center gap-1 text-xs font-semibold text-brand-600 hover:text-brand-700"
        >
          View all <ArrowRight size={13} />
        </Link>
      </div>

      {loading ? (
        <div className="space-y-3 p-5">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="h-14 animate-pulse rounded-lg bg-slate-100" />
          ))}
        </div>
      ) : alerts.length === 0 ? (
        <div className="grid flex-1 place-items-center px-5 py-10 text-center">
          <div>
            <span className="mx-auto grid h-11 w-11 place-items-center rounded-full bg-emerald-50 text-emerald-600">
              <CheckCircle size={22} />
            </span>
            <p className="mt-3 text-sm font-medium text-slate-700">Nothing needs attention</p>
            <p className="mt-1 text-xs text-slate-500">
              Alerts appear here when a check fails or a certificate nears expiry.
            </p>
          </div>
        </div>
      ) : (
        <ul className="thin-scroll max-h-[302px] flex-1 divide-y divide-slate-100 overflow-y-auto px-5">
          {alerts.map((alert) => {
            const tone = SEVERITY[alert.severity] || SEVERITY.info;
            const Icon = tone.icon;
            return (
              <li key={alert.id} className="flex items-start gap-3 py-3.5">
                <span className={`grid h-9 w-9 shrink-0 place-items-center rounded-full ${tone.wrap}`}>
                  <Icon size={18} />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-start gap-2">
                    <p className="min-w-0 flex-1 truncate text-[13px] font-semibold text-slate-800">
                      {alert.title}
                    </p>
                    <span
                      className={`shrink-0 rounded-md px-1.5 py-0.5 text-[10px] font-bold ${tone.badge}`}
                    >
                      {tone.label}
                    </span>
                  </div>
                  <p className="mt-0.5 flex items-center gap-1.5 text-xs text-slate-500">
                    <span className="truncate">{alert.target}</span>
                    <span className="shrink-0 text-slate-300">·</span>
                    <span className="shrink-0">{relativeTime(alert.at)}</span>
                  </p>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
